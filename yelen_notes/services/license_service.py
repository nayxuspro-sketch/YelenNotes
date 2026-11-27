"""Service cryptographique de gestion des licences d'utilisation et d'empreinte matérielle.

Architecture de sécurité (100 % hors ligne, bibliothèque standard Python 3.11+) :
- Signature asymétrique RSA-2048 (RSASSA-PKCS1-v1_5 + SHA-256, RFC 8017) :
  * La Version Client distribuée aux écoles embarque uniquement la clé publique (N, E).
  * Seule la Version de Déploiement du Super Admin (`config/yelen_deployment_edition.json`)
    détient l'exposant privé RSA-2048 permettant de signer des licences.
- Liaison matérielle stricte (`YNM-XXXX-XXXX-XXXX-XXXX-XXXX`) calculée à la volée à partir des
  identifiants matériels/système de l'ordinateur hôte (jamais stockée comme simple jeton copiable) :
  toute copie du dossier applicatif ou des données vers un autre ordinateur échoue immédiatement.
- Deux types de licences :
  * `MONOPOSTE` : exécution locale uniquement (`monoposte` / `127.0.0.1`), bloque le multiposte réseau.
  * `MULTIPOSTE` : autorise le mode serveur réseau local (`multiposte`) ainsi que le mode monoposte
    sur l'ordinateur serveur licencié.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import platform
import re
import secrets
import socket
import subprocess
import uuid
import zipfile
from typing import Any

from yelen_notes.models import LICENSE_MONOPOSTE, LICENSE_MULTIPOSTE, LICENSE_TYPES
from yelen_notes.paths import RuntimePaths

# Clé publique RSA-2048 éditeur par défaut (2048 bits, e = 65537)
DEFAULT_PUBLISHER_E = 65537
DEFAULT_PUBLISHER_N_HEX = (
    "b0861c16617149e27ec75c52653692b52c53241770ffb8d5aeff2c9975159b64"
    "e75cc0aaaefc371249bfa8c375abcc4bb6066e6283dd543ae66b2959ded20f5c"
    "64b97c143a124a3b590421bb760a4111f9fda99392336cd4e20263d618f0e28a"
    "7c8226d91f7bb6ada1eae81c64ec1b8fdb2ee550af6ce333679955d1deb4504f"
    "1bfc57fb6033b464b2ab689a0360394df547c34915c64ecf4634f221faf031a6"
    "98a7440c03f96a066bf5662760896c836a39f18bed78aba76f045eba0d5b12d8"
    "9aa0aa40ca1b7b0cc6c0b8c39a23882b226590069e6a77d84ae7e407fdabfdf5"
    "e2d3a9897ff5adf852d7491a8cc302559469b3b9971d4351225a6543d68f0b17"
)

# Préfixe DER DigestInfo SHA-256 conforme à la norme RFC 8017 §9.2 (RSASSA-PKCS1-v1_5)
_PKCS1_SHA256_DER_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")

DEPLOYMENT_KEY_FILENAME = "yelen_deployment_edition.json"
PUBLISHER_PUB_FILENAME = "yelen_publisher_pub.json"
CLIENT_LICENSE_FILENAME = "yelen_license.ylic"

LICENSE_TYPE_LABELS: dict[str, str] = {
    LICENSE_MONOPOSTE: "Licence Monoposte (poste unique local)",
    LICENSE_MULTIPOSTE: "Licence Multiposte Réseau (serveur école + postes LAN)",
}

_FINGERPRINT_REGEX = re.compile(r"^YNM-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}$")
_HOST_IDENTITY_CACHE: "MachineIdentity | None" = None


class LicenseError(ValueError):
    """Erreur métier lors de la génération, la lecture ou l'activation d'une licence."""


@dataclass(frozen=True)
class MachineIdentity:
    fingerprint: str
    hostname: str
    os_label: str
    sources_used: tuple[str, ...]


@dataclass(frozen=True)
class LicenseEvaluation:
    is_deployment_edition: bool
    allowed: bool
    status_code: str
    status_label: str
    reason: str
    current_machine_fingerprint: str
    hostname: str
    os_label: str
    license_payload: dict[str, Any] | None = None
    license_file_path: str | None = None

    @property
    def license_type(self) -> str | None:
        if self.is_deployment_edition:
            return "DEPLOIEMENT_SUPER_ADMIN"
        if self.license_payload:
            return str(self.license_payload.get("license_type") or "")
        return None

    @property
    def license_type_label(self) -> str:
        if self.is_deployment_edition:
            return "Version de déploiement (Super Admin)"
        lt = self.license_type or ""
        return LICENSE_TYPE_LABELS.get(lt, "Aucune licence active")


def normalize_machine_fingerprint(raw: str | None) -> str:
    """Normalise et valide une empreinte matérielle au format canonique `YNM-XXXX-XXXX-XXXX-XXXX-XXXX`."""
    cleaned = re.sub(r"[^0-9A-Fa-f]", "", (raw or "").strip().upper())
    if cleaned.startswith("YNM"):
        cleaned = cleaned[3:]
    elif (raw or "").strip().upper().startswith("YNM"):
        # Retirer uniquement le préfixe YNM avant nettoyage hexadécimal
        without_prefix = (raw or "").strip().upper()[3:]
        cleaned = re.sub(r"[^0-9A-F]", "", without_prefix)

    if len(cleaned) != 20 or not all(c in "0123456789ABCDEF" for c in cleaned):
        raise LicenseError(
            "Format d'empreinte matérielle invalide. Format attendu : YNM-XXXX-XXXX-XXXX-XXXX-XXXX "
            "(20 caractères hexadécimaux)."
        )
    groups = [cleaned[i : i + 4] for i in range(0, 20, 4)]
    formatted = "YNM-" + "-".join(groups)
    if not _FINGERPRINT_REGEX.match(formatted):
        raise LicenseError("Empreinte matérielle invalide.")
    return formatted


def _read_first_existing_text(candidates: list[Path]) -> str:
    for p in candidates:
        try:
            if p.is_file():
                val = p.read_text(encoding="utf-8", errors="ignore").strip()
                if val and val.lower() not in ("none", "to be filled by o.e.m.", "default string"):
                    return val
        except OSError:
            continue
    return ""


def _collect_windows_hardware_signals() -> list[tuple[str, str]]:
    signals: list[tuple[str, str]] = []
    # 1. MachineGuid du registre Windows (unique à l'installation Windows du poste)
    try:
        import winreg  # type: ignore[import-not-found]

        access = winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0, access
        ) as key:
            val, _ = winreg.QueryValueEx(key, "MachineGuid")
            if val and str(val).strip():
                signals.append(("win_machine_guid", str(val).strip().lower()))
    except Exception:
        pass

    # 2. UUID matériel carte mère SMBIOS (WMIC / CIM)
    try:
        out = subprocess.check_output(
            ["wmic", "csproduct", "get", "uuid"],
            stderr=subprocess.DEVNULL,
            timeout=2.0,
            text=True,
        )
        lines = [ln.strip() for ln in out.splitlines() if ln.strip() and ln.strip().upper() != "UUID"]
        if lines and lines[0] != "FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF":
            signals.append(("smbios_uuid", lines[0].lower()))
    except Exception:
        pass

    # 3. Numéro de série du volume système Windows (ex. C:\)
    try:
        import ctypes

        sys_drive = (os.environ.get("SystemDrive") or "C:") + "\\"
        vol_serial = ctypes.c_ulong(0)
        ok = ctypes.windll.kernel32.GetVolumeInformationW(  # type: ignore[attr-defined]
            ctypes.c_wchar_p(sys_drive),
            None,
            0,
            ctypes.byref(vol_serial),
            None,
            None,
            None,
            0,
        )
        if ok and vol_serial.value:
            signals.append(("sys_vol_serial", f"{vol_serial.value:08x}"))
    except Exception:
        pass

    proc_id = os.environ.get("PROCESSOR_IDENTIFIER", "").strip()
    if proc_id:
        signals.append(("cpu_id", proc_id.lower()))
    return signals


def _collect_unix_hardware_signals() -> list[tuple[str, str]]:
    signals: list[tuple[str, str]] = []

    machine_id = _read_first_existing_text(
        [Path("/etc/machine-id"), Path("/var/lib/dbus/machine-id")]
    )
    if machine_id:
        signals.append(("os_machine_id", machine_id.lower()))

    dmi_uuid = _read_first_existing_text(
        [
            Path("/sys/class/dmi/id/product_uuid"),
            Path("/sys/class/dmi/id/board_serial"),
            Path("/proc/device-tree/serial-number"),
        ]
    )
    if dmi_uuid:
        signals.append(("hw_dmi", dmi_uuid.lower()))

    if platform.system() == "Darwin":
        try:
            out = subprocess.check_output(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                stderr=subprocess.DEVNULL,
                timeout=2.0,
                text=True,
            )
            m = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', out)
            if m:
                signals.append(("mac_platform_uuid", m.group(1).lower()))
        except Exception:
            pass

    # Périphérique / inode racine du système de fichiers hôte (diffère d'une machine à l'autre)
    try:
        st_root = os.stat("/")
        if st_root.st_dev:
            signals.append(("root_fs_dev", f"{st_root.st_dev:x}"))
    except OSError:
        pass

    return signals


def get_machine_identity(fingerprint_override: str | None = None) -> MachineIdentity:
    """Calcule l'empreinte unique de l'ordinateur hôte (mise en cache mémoire pour le processus)."""
    global _HOST_IDENTITY_CACHE

    if fingerprint_override:
        norm = normalize_machine_fingerprint(fingerprint_override)
        return MachineIdentity(
            fingerprint=norm,
            hostname=socket.gethostname() or "poste-local",
            os_label=f"{platform.system()} {platform.release()} ({platform.machine()})",
            sources_used=("simulation_test",),
        )

    if _HOST_IDENTITY_CACHE is not None:
        return _HOST_IDENTITY_CACHE

    signals: list[tuple[str, str]] = []
    if os.name == "nt":
        signals.extend(_collect_windows_hardware_signals())
    else:
        signals.extend(_collect_unix_hardware_signals())

    # Adresse MAC matérielle non aléatoire (bit multicast 40 == 0)
    node = uuid.getnode()
    if ((node >> 40) & 1) == 0 and node != 0:
        signals.append(("hw_mac", f"{node:012x}"))

    signals.append(("sys_arch", f"{platform.system().lower()}:{platform.machine().lower()}"))
    if len(signals) <= 1:
        # Repli ultime sur le nom d'hôte système si l'environnement est ultra-restreint
        signals.append(("hostname", (socket.gethostname() or "unknown-host").strip().lower()))

    canonical_material = "|".join(f"{k}={v}" for k, v in signals).encode("utf-8")
    digest_hex = hmac.new(
        b"YELEN-NOTES-HARDWARE-BINDING-KEY-V1",
        canonical_material,
        hashlib.sha256,
    ).hexdigest().upper()[:20]

    groups = [digest_hex[i : i + 4] for i in range(0, 20, 4)]
    fp = "YNM-" + "-".join(groups)

    identity = MachineIdentity(
        fingerprint=fp,
        hostname=socket.gethostname() or "poste-local",
        os_label=f"{platform.system()} {platform.release()} ({platform.machine()})",
        sources_used=tuple(k for k, _ in signals),
    )
    _HOST_IDENTITY_CACHE = identity
    return identity


# ============================================================================
# Moteur cryptographique RSA-2048 pur Python (RFC 8017 RSASSA-PKCS1-v1_5)
# ============================================================================


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(txt: str) -> bytes:
    pad = "=" * ((4 - (len(txt) % 4)) % 4)
    return base64.urlsafe_b64decode((txt + pad).encode("ascii"))


def _emsa_pkcs1_v1_5_encode_sha256(message: bytes, em_len: int) -> bytes:
    digest = hashlib.sha256(message).digest()
    t = _PKCS1_SHA256_DER_PREFIX + digest
    if em_len < len(t) + 11:
        raise LicenseError("Taille de module RSA insuffisante.")
    ps = b"\xff" * (em_len - len(t) - 3)
    return b"\x00\x01" + ps + b"\x00" + t


def _rsa_sign_sha256(message: bytes, n: int, d: int) -> bytes:
    k = (n.bit_length() + 7) // 8
    em = _emsa_pkcs1_v1_5_encode_sha256(message, k)
    m_int = int.from_bytes(em, "big")
    s_int = pow(m_int, d, n)
    return s_int.to_bytes(k, "big")


def _rsa_verify_sha256(message: bytes, signature: bytes, n: int, e: int) -> bool:
    k = (n.bit_length() + 7) // 8
    if len(signature) != k:
        return False
    s_int = int.from_bytes(signature, "big")
    if s_int <= 0 or s_int >= n:
        return False
    m_int = pow(s_int, e, n)
    em = m_int.to_bytes(k, "big")
    expected_em = _emsa_pkcs1_v1_5_encode_sha256(message, k)
    return hmac.compare_digest(em, expected_em)


def _is_probable_prime(n: int, rounds: int = 18) -> bool:
    if n < 2 or n % 2 == 0:
        return n == 2
    for small_p in (3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53, 59, 61, 67, 71, 73):
        if n == small_p:
            return True
        if n % small_p == 0:
            return False
    r, d_val = 0, n - 1
    while d_val % 2 == 0:
        r += 1
        d_val //= 2
    for _ in range(rounds):
        a = secrets.randbelow(n - 3) + 2
        x = pow(a, d_val, n)
        if x == 1 or x == n - 1:
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _generate_prime(bits: int = 1024) -> int:
    while True:
        cand = secrets.randbits(bits) | (1 << (bits - 1)) | 1
        if _is_probable_prime(cand):
            return cand


def regenerate_publisher_keypair(paths: RuntimePaths) -> str:
    """Génère une nouvelle paire de clés maîtresse RSA-2048 pour l'éditeur (Super Admin)."""
    e = DEFAULT_PUBLISHER_E
    while True:
        p = _generate_prime(1024)
        q = _generate_prime(1024)
        if p == q:
            continue
        phi = (p - 1) * (q - 1)
        try:
            d = pow(e, -1, phi)
        except ValueError:
            continue
        n = p * q
        if n.bit_length() == 2048:
            break

    key_id = f"YELEN-PUB-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"
    n_hex = format(n, "x")
    d_hex = format(d, "x")

    paths.config_dir.mkdir(parents=True, exist_ok=True)
    (paths.config_dir / PUBLISHER_PUB_FILENAME).write_text(
        json.dumps(
            {"schema": "YELEN-PUBLISHER-PUB-V1", "key_id": key_id, "e": e, "n_hex": n_hex},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (paths.config_dir / DEPLOYMENT_KEY_FILENAME).write_text(
        json.dumps(
            {
                "schema": "YELEN-DEPLOYMENT-EDITION-V1",
                "edition": "SUPER_ADMIN_DEPLOYMENT",
                "key_id": key_id,
                "e": e,
                "n_hex": n_hex,
                "d_hex": d_hex,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return key_id


def load_publisher_public_key(paths: RuntimePaths) -> tuple[int, int, str]:
    """Charge la clé publique RSA-2048 de vérification (fichier éditeur ou clé intégrée)."""
    for pub_file in (
        paths.config_dir / PUBLISHER_PUB_FILENAME,
        paths.program_root / "_internal" / "config" / PUBLISHER_PUB_FILENAME,
    ):
        if pub_file.is_file():
            try:
                data = json.loads(pub_file.read_text(encoding="utf-8"))
                n = int(str(data["n_hex"]), 16)
                e = int(data.get("e", DEFAULT_PUBLISHER_E))
                key_id = str(data.get("key_id", "YELEN-PUB-CUSTOM"))
                if n.bit_length() >= 2048 and e >= 3:
                    return n, e, key_id
            except Exception:
                continue
    return int(DEFAULT_PUBLISHER_N_HEX, 16), DEFAULT_PUBLISHER_E, "YELEN-PUB-2026"


def _load_valid_deployment_private_key(paths: RuntimePaths) -> tuple[int, int, int, str] | None:
    """Vérifie mathématiquement la clé privée de la Version de Déploiement Super Admin."""
    dep_file = paths.config_dir / DEPLOYMENT_KEY_FILENAME
    if not dep_file.is_file():
        return None
    try:
        data = json.loads(dep_file.read_text(encoding="utf-8"))
        if data.get("schema") != "YELEN-DEPLOYMENT-EDITION-V1":
            return None
        n = int(str(data["n_hex"]), 16)
        e = int(data.get("e", DEFAULT_PUBLISHER_E))
        d = int(str(data["d_hex"]), 16)
        key_id = str(data.get("key_id", "YELEN-PUB-2026"))
        pub_n, pub_e, _ = load_publisher_public_key(paths)
        if n != pub_n or e != pub_e or n.bit_length() < 2048:
            return None
        # Preuve cryptographique : rejette tout fichier factice ne possédant pas le vrai exposant privé d
        challenge = 0x59454C454E5F4445504C4F595F434845434B
        if pow(pow(challenge, d, n), e, n) != challenge:
            return None
        return n, e, d, key_id
    except Exception:
        return None


def is_deployment_edition(paths: RuntimePaths, *, force_client: bool = False) -> bool:
    """Indique si cette instance est la Version de Déploiement authentique du Super Admin."""
    if force_client:
        return False
    return _load_valid_deployment_private_key(paths) is not None


# ============================================================================
# Émission, vérification et installation des licences (.ylic / YNL1-...)
# ============================================================================


def generate_signed_license(
    paths: RuntimePaths,
    *,
    license_type: str,
    beneficiary_name: str,
    machine_fingerprint: str,
    expires_at: date | None = None,
    serial: str | None = None,
) -> tuple[str, str, dict[str, Any]]:
    """Signe une licence Monoposte ou Multiposte Réseau liée à l'empreinte unique d'un ordinateur."""
    priv = _load_valid_deployment_private_key(paths)
    if priv is None:
        raise LicenseError(
            "Émission impossible : seule la Version de Déploiement du Super Admin détient la clé privée de signature."
        )
    n, _e, d, key_id = priv

    clean_type = (license_type or "").strip().upper()
    if clean_type not in LICENSE_TYPES:
        raise LicenseError("Type de licence invalide (choix autorisés : MONOPOSTE ou MULTIPOSTE).")

    clean_beneficiary = (beneficiary_name or "").strip()
    if not clean_beneficiary:
        raise LicenseError("Le nom de l'établissement ou du bénéficiaire est obligatoire.")

    norm_fp = normalize_machine_fingerprint(machine_fingerprint)
    lic_serial = serial or f"YN-{datetime.utcnow().strftime('%Y')}-{secrets.token_hex(4).upper()}"

    payload: dict[str, Any] = {
        "schema": "YELEN-LICENSE-V1",
        "serial": lic_serial,
        "license_type": clean_type,
        "beneficiary_name": clean_beneficiary[:200],
        "machine_fingerprint": norm_fp,
        "issued_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": expires_at.isoformat() if expires_at else None,
        "key_id": key_id,
    }
    payload_bytes = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )
    signature = _rsa_sign_sha256(payload_bytes, n, d)
    token = f"YNL1.{_b64url_encode(payload_bytes)}.{_b64url_encode(signature)}"
    return lic_serial, token, payload


def build_ylic_file_content(token: str, payload: dict[str, Any]) -> bytes:
    """Construit le fichier `.ylic` lisible et transportable hors ligne."""
    doc = {
        "format": "Yelen Notes Official License File (.ylic)",
        "serial": payload.get("serial"),
        "license_type": payload.get("license_type"),
        "license_type_label": LICENSE_TYPE_LABELS.get(str(payload.get("license_type")), ""),
        "beneficiary_name": payload.get("beneficiary_name"),
        "machine_fingerprint": payload.get("machine_fingerprint"),
        "issued_at": payload.get("issued_at"),
        "expires_at": payload.get("expires_at"),
        "token": token,
    }
    return (json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def extract_token_from_input(raw_input: str) -> str:
    """Extrait le jeton `YNL1....` depuis un texte collé ou le contenu JSON d'un fichier `.ylic`."""
    txt = (raw_input or "").strip()
    if not txt:
        raise LicenseError("Veuillez fournir un fichier de licence (.ylic) ou une clé YNL1.")
    if txt.startswith("{"):
        try:
            parsed = json.loads(txt)
            tok = str(parsed.get("token") or "").strip()
            if tok:
                return re.sub(r"\s+", "", tok)
        except Exception as exc:
            raise LicenseError("Le fichier .ylic fourni est mal formé ou corrompu.") from exc
    compact = re.sub(r"\s+", "", txt)
    if not compact.startswith("YNL1."):
        raise LicenseError("Format de clé de licence non reconnu (doit commencer par YNL1.).")
    return compact


def verify_license_token_signature(paths: RuntimePaths, raw_token_or_ylic: str) -> tuple[str, dict[str, Any]]:
    """Vérifie la signature RSA-2048 d'une licence et retourne `(token_nettoye, payload)`."""
    token = extract_token_from_input(raw_token_or_ylic)
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != "YNL1":
        raise LicenseError("Structure du jeton de licence invalide.")
    try:
        payload_bytes = _b64url_decode(parts[1])
        sig_bytes = _b64url_decode(parts[2])
        payload = json.loads(payload_bytes.decode("utf-8"))
    except Exception as exc:
        raise LicenseError("Contenu de la licence illisible ou altéré.") from exc

    if not isinstance(payload, dict) or payload.get("schema") != "YELEN-LICENSE-V1":
        raise LicenseError("Schéma de licence non supporté.")

    pub_n, pub_e, _ = load_publisher_public_key(paths)
    if not _rsa_verify_sha256(payload_bytes, sig_bytes, pub_n, pub_e):
        raise LicenseError("Signature cryptographique invalide : cette licence a été falsifiée ou n'a pas été émise par le Super Admin.")

    lic_type = str(payload.get("license_type") or "")
    if lic_type not in LICENSE_TYPES:
        raise LicenseError("Type de licence inconnu dans le jeton signé.")
    normalize_machine_fingerprint(str(payload.get("machine_fingerprint") or ""))
    return token, payload


def get_client_license_paths(paths: RuntimePaths) -> list[Path]:
    """Emplacements recherchés pour le fichier de licence local."""
    return [
        paths.config_dir / CLIENT_LICENSE_FILENAME,
        paths.data_dir / "license" / CLIENT_LICENSE_FILENAME,
    ]


def install_license_on_machine(
    paths: RuntimePaths,
    raw_token_or_ylic: str,
    *,
    fingerprint_override: str | None = None,
) -> dict[str, Any]:
    """Vérifie qu'une licence correspond exactement à CET ordinateur puis l'installe."""
    token, payload = verify_license_token_signature(paths, raw_token_or_ylic)
    ident = get_machine_identity(fingerprint_override)
    lic_fp = normalize_machine_fingerprint(str(payload.get("machine_fingerprint") or ""))

    if lic_fp != ident.fingerprint:
        raise LicenseError(
            f"Activation refusée : cette licence est liée à l'ordinateur « {lic_fp} », "
            f"alors que l'empreinte de cet ordinateur est « {ident.fingerprint} »."
        )

    exp_raw = payload.get("expires_at")
    if exp_raw:
        exp_date = date.fromisoformat(str(exp_raw))
        if date.today() > exp_date:
            raise LicenseError(f"Cette licence est expirée depuis le {exp_date.strftime('%d/%m/%Y')}.")

    content = build_ylic_file_content(token, payload)
    written = False
    for target in get_client_license_paths(paths):
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            written = True
        except OSError:
            continue
    if not written:
        raise LicenseError("Impossible d'écrire le fichier de licence sur le disque local.")
    return payload


def _is_loopback_address(remote_addr: str | None) -> bool:
    if not remote_addr:
        return True
    addr = remote_addr.strip().lower()
    if addr in ("127.0.0.1", "::1", "localhost"):
        return True
    if addr.startswith("::ffff:127.") or addr.startswith("127."):
        return True
    return False


def _is_serial_revoked_in_db(serial: str | None) -> bool:
    if not serial:
        return False
    try:
        from sqlalchemy import inspect
        from yelen_notes.extensions import db
        from yelen_notes.models import IssuedLicense

        insp = inspect(db.engine)
        if insp.has_table("issued_licenses"):
            rec = IssuedLicense.query.filter_by(license_serial=serial, is_revoked=True).first()
            return rec is not None
    except Exception:
        return False
    return False


def evaluate_runtime_license(
    paths: RuntimePaths,
    *,
    server_mode: str,
    remote_addr: str | None = None,
    force_client: bool = False,
    fingerprint_override: str | None = None,
) -> LicenseEvaluation:
    """Évalue l'état complet de la licence pour la machine courante et le mode d'exécution."""
    ident = get_machine_identity(fingerprint_override)
    dep_edition = is_deployment_edition(paths, force_client=force_client)

    # Lecture optionnelle d'une licence locale déjà présente sur le disque
    installed_file: Path | None = None
    for cand in get_client_license_paths(paths):
        if cand.is_file():
            installed_file = cand
            break

    if dep_edition:
        parsed_payload: dict[str, Any] | None = None
        if installed_file is not None:
            try:
                _, parsed_payload = verify_license_token_signature(
                    paths, installed_file.read_text(encoding="utf-8")
                )
            except Exception:
                parsed_payload = None
        return LicenseEvaluation(
            is_deployment_edition=True,
            allowed=True,
            status_code="DEPLOYMENT_EDITION",
            status_label="Version de déploiement Super Admin active",
            reason="Édition de déploiement authentifiée : accès complet et signature de licences activés.",
            current_machine_fingerprint=ident.fingerprint,
            hostname=ident.hostname,
            os_label=ident.os_label,
            license_payload=parsed_payload,
            license_file_path=str(installed_file) if installed_file else None,
        )

    # Mode Poste Client : une licence signée valide pour CET ordinateur est obligatoire
    if installed_file is None:
        return LicenseEvaluation(
            is_deployment_edition=False,
            allowed=False,
            status_code="MISSING",
            status_label="Aucune licence installée",
            reason=(
                "Ce poste ne dispose d'aucune licence d'utilisation active. "
                "Veuillez transmettre l'empreinte matérielle ci-dessous au Super Admin pour obtenir votre licence."
            ),
            current_machine_fingerprint=ident.fingerprint,
            hostname=ident.hostname,
            os_label=ident.os_label,
        )

    try:
        raw_content = installed_file.read_text(encoding="utf-8")
        _, payload = verify_license_token_signature(paths, raw_content)
    except LicenseError as exc:
        return LicenseEvaluation(
            is_deployment_edition=False,
            allowed=False,
            status_code="SIGNATURE_INVALID",
            status_label="Licence invalide ou altérée",
            reason=str(exc),
            current_machine_fingerprint=ident.fingerprint,
            hostname=ident.hostname,
            os_label=ident.os_label,
            license_file_path=str(installed_file),
        )
    except OSError:
        return LicenseEvaluation(
            is_deployment_edition=False,
            allowed=False,
            status_code="MISSING",
            status_label="Fichier de licence inaccessible",
            reason="Impossible de lire le fichier de licence local.",
            current_machine_fingerprint=ident.fingerprint,
            hostname=ident.hostname,
            os_label=ident.os_label,
        )

    if _is_serial_revoked_in_db(str(payload.get("serial") or "")):
        return LicenseEvaluation(
            is_deployment_edition=False,
            allowed=False,
            status_code="REVOKED",
            status_label="Licence révoquée",
            reason="Cette licence a été révoquée par le Super Admin.",
            current_machine_fingerprint=ident.fingerprint,
            hostname=ident.hostname,
            os_label=ident.os_label,
            license_payload=payload,
            license_file_path=str(installed_file),
        )

    # Anti-copie : vérification stricte de l'empreinte unique de l'ordinateur
    lic_fp = normalize_machine_fingerprint(str(payload.get("machine_fingerprint") or ""))
    if lic_fp != ident.fingerprint:
        return LicenseEvaluation(
            is_deployment_edition=False,
            allowed=False,
            status_code="MACHINE_MISMATCH",
            status_label="Copie sur un autre ordinateur détectée",
            reason=(
                f"Cette copie du dossier applicatif ne peut pas fonctionner sur cet ordinateur : "
                f"la licence installée appartient exclusivement à l'empreinte « {lic_fp} », "
                f"différente de l'empreinte unique de ce poste (« {ident.fingerprint} »)."
            ),
            current_machine_fingerprint=ident.fingerprint,
            hostname=ident.hostname,
            os_label=ident.os_label,
            license_payload=payload,
            license_file_path=str(installed_file),
        )

    # Expiration éventuelle
    exp_raw = payload.get("expires_at")
    if exp_raw:
        try:
            exp_date = date.fromisoformat(str(exp_raw))
            if date.today() > exp_date:
                return LicenseEvaluation(
                    is_deployment_edition=False,
                    allowed=False,
                    status_code="EXPIRED",
                    status_label="Licence expirée",
                    reason=f"Votre licence a expiré le {exp_date.strftime('%d/%m/%Y')}. Contactez le Super Admin.",
                    current_machine_fingerprint=ident.fingerprint,
                    hostname=ident.hostname,
                    os_label=ident.os_label,
                    license_payload=payload,
                    license_file_path=str(installed_file),
                )
        except ValueError:
            return LicenseEvaluation(
                is_deployment_edition=False,
                allowed=False,
                status_code="SIGNATURE_INVALID",
                status_label="Date d'expiration corrompue",
                reason="Format de date d'expiration invalide dans la licence.",
                current_machine_fingerprint=ident.fingerprint,
                hostname=ident.hostname,
                os_label=ident.os_label,
                license_payload=payload,
                license_file_path=str(installed_file),
            )

    # Contrôle du périmètre d'exploitation : Monoposte vs Multiposte Réseau
    lic_type = str(payload.get("license_type") or "")
    active_mode = (server_mode or "monoposte").strip().lower()
    if lic_type == LICENSE_MONOPOSTE:
        if active_mode == "multiposte":
            return LicenseEvaluation(
                is_deployment_edition=False,
                allowed=False,
                status_code="MODE_FORBIDDEN",
                status_label="Mode Multiposte Réseau non autorisé",
                reason=(
                    "Ce poste dispose d'une Licence Monoposte uniquement : le démarrage en mode "
                    "Multiposte Réseau requiert une Licence Multiposte Réseau délivrée par le Super Admin."
                ),
                current_machine_fingerprint=ident.fingerprint,
                hostname=ident.hostname,
                os_label=ident.os_label,
                license_payload=payload,
                license_file_path=str(installed_file),
            )
        if not _is_loopback_address(remote_addr):
            return LicenseEvaluation(
                is_deployment_edition=False,
                allowed=False,
                status_code="NETWORK_FORBIDDEN",
                status_label="Accès réseau local refusé (Licence Monoposte)",
                reason=(
                    "Accès réseau distant bloqué : la Licence Monoposte installée n'autorise que "
                    "l'utilisation locale sur l'ordinateur licencié (127.0.0.1)."
                ),
                current_machine_fingerprint=ident.fingerprint,
                hostname=ident.hostname,
                os_label=ident.os_label,
                license_payload=payload,
                license_file_path=str(installed_file),
            )

    return LicenseEvaluation(
        is_deployment_edition=False,
        allowed=True,
        status_code="VALID",
        status_label=LICENSE_TYPE_LABELS.get(lic_type, "Licence active"),
        reason="Licence authentique et conforme à l'empreinte matérielle de cet ordinateur.",
        current_machine_fingerprint=ident.fingerprint,
        hostname=ident.hostname,
        os_label=ident.os_label,
        license_payload=payload,
        license_file_path=str(installed_file),
    )


def convert_current_installation_to_client(
    paths: RuntimePaths,
    *,
    server_mode: str,
    fingerprint_override: str | None = None,
) -> None:
    """Verrouille l'installation courante en Poste Client en supprimant la clé de déploiement Super Admin.

    Par sécurité, exige qu'une licence Client valide pour CET ordinateur et ce mode soit déjà installée.
    """
    sim_eval = evaluate_runtime_license(
        paths,
        server_mode=server_mode,
        force_client=True,
        fingerprint_override=fingerprint_override,
    )
    if not sim_eval.allowed:
        raise LicenseError(
            "Verrouillage annulé par sécurité : installez d'abord sur ce poste une licence valide "
            f"correspondant à son empreinte ({sim_eval.current_machine_fingerprint}) avant de retirer "
            "la clé de déploiement Super Admin."
        )
    dep_file = paths.config_dir / DEPLOYMENT_KEY_FILENAME
    if dep_file.is_file():
        dep_file.unlink()


def build_client_school_kit_zip(paths: RuntimePaths, app_version: str) -> bytes:
    """Génère une archive ZIP propre « Version Client École » sans la clé privée de déploiement."""
    root = paths.program_root
    prefix = f"YelenNotes-Client-v{app_version}"

    include_top_files = [
        "run.py",
        "VERSION.txt",
        "README.txt",
        "README.md",
        "requirements.txt",
        "requirements-build.txt",
        "1_Lancer_Yelen_Notes.vbs",
        "Lancer_Multiposte_Reseau.vbs",
        "Lancer_Demonstration.vbs",
        "Arreter_Yelen_Notes.vbs",
        "1_Installer_et_Lancer_Directement.bat",
        "2_Compiler_Dossier_Portable_EXE.bat",
    ]
    include_dirs = ["migrations", "packaging", "resources", "yelen_notes"]
    excluded_dir_names = {"__pycache__", ".pytest_cache", ".mypy_cache", ".git", ".venv", "venv", "dist", "build"}
    excluded_suffixes = {".pyc", ".pyo", ".db", ".db-wal", ".db-shm", ".log", ".ylic"}

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for fname in include_top_files:
            fpath = root / fname
            if fpath.is_file():
                zf.write(fpath, f"{prefix}/{fname}")

        # Dossier config/ : inclure uniquement l'exemple .ini et la clé PUBLIQUE de vérification
        example_ini = root / "config" / "yelen_notes.example.ini"
        if example_ini.is_file():
            zf.write(example_ini, f"{prefix}/config/yelen_notes.example.ini")

        pub_n, pub_e, key_id = load_publisher_public_key(paths)
        pub_json = json.dumps(
            {
                "schema": "YELEN-PUBLISHER-PUB-V1",
                "key_id": key_id,
                "e": pub_e,
                "n_hex": format(pub_n, "x"),
            },
            indent=2,
        ) + "\n"
        zf.writestr(f"{prefix}/config/{PUBLISHER_PUB_FILENAME}", pub_json)

        activation_guide = (
            "========================================================================\r\n"
            f"  YELEN NOTES v{app_version} — GUIDE D'ACTIVATION POSTE CLIENT ÉCOLE\r\n"
            "========================================================================\r\n\r\n"
            "Cette version est protégée par licence matérielle individuelle.\r\n"
            "Toute copie de ce dossier vers un autre ordinateur sera automatiquement bloquée.\r\n\r\n"
            "PROCÉDURE D'ACTIVATION :\r\n"
            "1. Double-cliquez sur « 1_Lancer_Yelen_Notes.vbs » (aucune fenêtre console :\r\n"
            "   l'application démarre silencieusement en arrière-plan et ouvre le navigateur).\r\n"
            "2. Sur l'écran d'activation qui s'ouvre dans le navigateur, relevez le\r\n"
            "   Code d'Empreinte Matérielle Unique (format : YNM-XXXX-XXXX-XXXX-XXXX-XXXX)\r\n"
            "   ou téléchargez la fiche de demande d'activation (.req).\r\n"
            "3. Transmettez cette empreinte au Super Administrateur Yelen Notes afin de\r\n"
            "   recevoir votre licence signée (Monoposte ou Multiposte Réseau).\r\n"
            "4. Importez le fichier .ylic reçu (ou collez la clé YNL1-...) pour déverrouiller\r\n"
            "   définitivement l'application sur cet ordinateur.\r\n"
        )
        zf.writestr(f"{prefix}/LISEZ_MOI_ACTIVATION_LICENCE.txt", activation_guide.encode("utf-8"))

        for dname in include_dirs:
            base_dir = root / dname
            if not base_dir.is_dir():
                continue
            for item in sorted(base_dir.rglob("*")):
                if any(part in excluded_dir_names for part in item.parts):
                    continue
                if item.is_file():
                    if item.suffix.lower() in excluded_suffixes:
                        continue
                    if item.name == DEPLOYMENT_KEY_FILENAME:
                        continue
                    rel = item.relative_to(root).as_posix()
                    zf.write(item, f"{prefix}/{rel}")

    return buf.getvalue()

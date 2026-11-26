"""Module de gestion des licences d'utilisation (exclusivement réservé au Super Admin)
et écran de verrouillage / activation matérielle des postes clients."""

from __future__ import annotations

from datetime import datetime
from functools import wraps
import io
import json
from typing import Any, Callable

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from flask_login import current_user

from yelen_notes.extensions import db
from yelen_notes.models import (
    LICENSE_MONOPOSTE,
    LICENSE_MULTIPOSTE,
    IssuedLicense,
    utcnow_naive,
)
from yelen_notes.paths import RuntimePaths
from yelen_notes.security import (
    get_client_ip,
    is_ajax_or_json_request,
    is_super_admin,
    record_audit_log,
)
from yelen_notes.services.license_service import (
    LICENSE_TYPE_LABELS,
    LicenseError,
    build_client_school_kit_zip,
    build_ylic_file_content,
    convert_current_installation_to_client,
    evaluate_runtime_license,
    generate_signed_license,
    get_machine_identity,
    install_license_on_machine,
    is_deployment_edition,
    load_publisher_public_key,
    regenerate_publisher_keypair,
    verify_license_token_signature,
)

licensing_bp = Blueprint("licensing", __name__)


def super_admin_only(view_func: Callable) -> Callable:
    """Garantit côté serveur un accès strictement exclusif au rôle SUPER_ADMIN."""

    @wraps(view_func)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        if not current_user.is_authenticated:
            if is_ajax_or_json_request():
                return jsonify({"ok": False, "error": "Authentification Super Admin requise."}), 401
            return redirect(url_for("auth.login", next=request.full_path))
        if (
            not current_user.is_active
            or current_user.is_suspended()
            or current_user.is_locked()
            or not is_super_admin(current_user)
        ):
            record_audit_log(
                "ACCESS_DENIED",
                f"Accès refusé au module Super Admin des licences ({request.path}) pour {current_user.username}",
                module="licensing",
                status="REFUSED",
                commit=True,
            )
            abort(403)
        return view_func(*args, **kwargs)

    return wrapped


def _current_license_eval():
    paths: RuntimePaths = current_app.config["RUNTIME_PATHS"]
    return evaluate_runtime_license(
        paths,
        server_mode=str(current_app.config.get("SERVER_MODE", "monoposte")),
        remote_addr=get_client_ip(),
        force_client=bool(current_app.config.get("FORCE_CLIENT_EDITION", False)),
        fingerprint_override=current_app.config.get("TEST_MACHINE_FINGERPRINT_OVERRIDE"),
    )


# ============================================================================
# 1. Écran public de verrouillage / activation matérielle du poste client
# ============================================================================


@licensing_bp.route("/licence/activation", methods=["GET"])
def activation_screen():
    lic_eval = _current_license_eval()
    return render_template(
        "licensing/activate.html",
        lic_eval=lic_eval,
        server_mode=current_app.config.get("SERVER_MODE", "monoposte"),
    )


@licensing_bp.route("/licence/demande-activation.req", methods=["GET"])
def download_machine_request():
    fp_override = current_app.config.get("TEST_MACHINE_FINGERPRINT_OVERRIDE")
    ident = get_machine_identity(fp_override)
    req_doc = {
        "schema": "YELEN-ACTIVATION-REQUEST-V1",
        "machine_fingerprint": ident.fingerprint,
        "hostname": ident.hostname,
        "os_label": ident.os_label,
        "requested_mode": str(current_app.config.get("SERVER_MODE", "monoposte")).upper(),
        "generated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    safe_host = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in ident.hostname)[:40]
    filename = f"demande_licence_{safe_host or 'poste'}.req"
    raw = (json.dumps(req_doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    return send_file(
        io.BytesIO(raw),
        mimetype="application/json",
        as_attachment=True,
        download_name=filename,
    )


@licensing_bp.route("/licence/activer", methods=["POST"])
def activate_submit():
    paths: RuntimePaths = current_app.config["RUNTIME_PATHS"]
    fp_override = current_app.config.get("TEST_MACHINE_FINGERPRINT_OVERRIDE")

    raw_input = ""
    uploaded = request.files.get("license_file")
    if uploaded and uploaded.filename:
        raw_input = uploaded.read().decode("utf-8", errors="replace")
    if not raw_input.strip():
        raw_input = (request.form.get("license_key") or "").strip()

    try:
        payload = install_license_on_machine(
            paths,
            raw_input,
            fingerprint_override=fp_override,
        )
    except LicenseError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("licensing.activation_screen"))

    try:
        record_audit_log(
            "LICENSE_ACTIVATE",
            (
                f"Activation de la licence {payload.get('serial')} ({payload.get('license_type')}) "
                f"pour « {payload.get('beneficiary_name')} » sur l'empreinte {payload.get('machine_fingerprint')}"
            ),
            module="licensing",
            commit=True,
        )
    except Exception:
        db.session.rollback()

    recheck = _current_license_eval()
    if not recheck.allowed:
        flash(
            f"Licence installée ({payload.get('serial')}), mais attention : {recheck.reason}",
            "warning",
        )
        return redirect(url_for("licensing.activation_screen"))

    flash(
        f"Licence {LICENSE_TYPE_LABELS.get(str(payload.get('license_type')), '')} activée avec succès "
        f"sur cet ordinateur ({recheck.current_machine_fingerprint}).",
        "success",
    )
    return redirect(url_for("dashboard.index"))


# ============================================================================
# 2. Module d'administration des licences (exclusivement Super Admin)
# ============================================================================


@licensing_bp.route("/administration/licences", methods=["GET"])
@super_admin_only
def manage_licenses():
    paths: RuntimePaths = current_app.config["RUNTIME_PATHS"]
    lic_eval = _current_license_eval()
    dep_active = is_deployment_edition(
        paths, force_client=bool(current_app.config.get("FORCE_CLIENT_EDITION", False))
    )
    _pub_n, _pub_e, pub_key_id = load_publisher_public_key(paths)

    issued_list = IssuedLicense.query.order_by(IssuedLicense.issued_at.desc(), IssuedLicense.id.desc()).all()
    last_created_id = session.pop("last_issued_license_id", None)
    last_created = db.session.get(IssuedLicense, int(last_created_id)) if last_created_id else None

    mono_count = sum(1 for item in issued_list if item.license_type == LICENSE_MONOPOSTE and not item.is_revoked)
    multi_count = sum(1 for item in issued_list if item.license_type == LICENSE_MULTIPOSTE and not item.is_revoked)

    return render_template(
        "admin/licenses.html",
        lic_eval=lic_eval,
        is_deployment_edition=dep_active,
        pub_key_id=pub_key_id,
        issued_licenses=issued_list,
        last_created=last_created,
        mono_count=mono_count,
        multi_count=multi_count,
        LICENSE_MONOPOSTE=LICENSE_MONOPOSTE,
        LICENSE_MULTIPOSTE=LICENSE_MULTIPOSTE,
        LICENSE_TYPE_LABELS=LICENSE_TYPE_LABELS,
    )


@licensing_bp.route("/administration/licences/generer", methods=["POST"])
@super_admin_only
def issue_license():
    paths: RuntimePaths = current_app.config["RUNTIME_PATHS"]
    if not is_deployment_edition(
        paths, force_client=bool(current_app.config.get("FORCE_CLIENT_EDITION", False))
    ):
        flash(
            "Action réservée à la Version de Déploiement du Super Admin (clé privée de signature absente sur ce poste client).",
            "danger",
        )
        return redirect(url_for("licensing.manage_licenses"))

    license_type = (request.form.get("license_type") or "").strip().upper()
    beneficiary_name = (request.form.get("beneficiary_name") or "").strip()
    machine_fingerprint = (request.form.get("machine_fingerprint") or "").strip()
    raw_expires = (request.form.get("expires_at") or "").strip()
    notes = (request.form.get("notes") or "").strip()[:255] or None
    install_here = request.form.get("install_on_current_pc") == "1"

    expires_date = None
    if raw_expires:
        try:
            expires_date = datetime.strptime(raw_expires, "%Y-%m-%d").date()
        except ValueError:
            flash("Format de date d'expiration invalide.", "danger")
            return redirect(url_for("licensing.manage_licenses"))

    try:
        serial, token, payload = generate_signed_license(
            paths,
            license_type=license_type,
            beneficiary_name=beneficiary_name,
            machine_fingerprint=machine_fingerprint,
            expires_at=expires_date,
        )
    except LicenseError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("licensing.manage_licenses"))

    rec = IssuedLicense(
        license_serial=serial,
        license_type=payload["license_type"],
        beneficiary_name=payload["beneficiary_name"],
        machine_fingerprint=payload["machine_fingerprint"],
        expires_at=expires_date,
        notes=notes,
        license_token=token,
        is_revoked=False,
        issued_by_id=current_user.id,
    )
    db.session.add(rec)
    db.session.flush()

    installed_msg = ""
    if install_here:
        try:
            install_license_on_machine(
                paths,
                token,
                fingerprint_override=current_app.config.get("TEST_MACHINE_FINGERPRINT_OVERRIDE"),
            )
            installed_msg = " et installée sur cet ordinateur"
        except LicenseError as exc:
            flash(
                f"Licence générée ({serial}), mais non installée localement : {exc}",
                "warning",
            )

    record_audit_log(
        "LICENSE_ISSUE",
        (
            f"Émission de la licence {serial} ({payload['license_type']}) pour « {payload['beneficiary_name']} » "
            f"[Empreinte : {payload['machine_fingerprint']}]"
        ),
        module="licensing",
        target_type="IssuedLicense",
        target_id=rec.id,
        commit=True,
    )
    session["last_issued_license_id"] = rec.id
    flash(
        f"Licence {serial} ({LICENSE_TYPE_LABELS.get(payload['license_type'], payload['license_type'])}) "
        f"générée avec succès{installed_msg}.",
        "success",
    )
    return redirect(url_for("licensing.manage_licenses"))


@licensing_bp.route("/administration/licences/<int:lic_id>/telecharger", methods=["GET"])
@super_admin_only
def download_issued_license(lic_id: int):
    paths: RuntimePaths = current_app.config["RUNTIME_PATHS"]
    rec = db.get_or_404(IssuedLicense, lic_id)
    try:
        token, payload = verify_license_token_signature(paths, rec.license_token)
    except LicenseError:
        payload = {
            "serial": rec.license_serial,
            "license_type": rec.license_type,
            "beneficiary_name": rec.beneficiary_name,
            "machine_fingerprint": rec.machine_fingerprint,
            "issued_at": rec.issued_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "expires_at": rec.expires_at.isoformat() if rec.expires_at else None,
        }
        token = rec.license_token

    ylic_bytes = build_ylic_file_content(token, payload)
    filename = f"licence_{rec.license_type.lower()}_{rec.license_serial}.ylic"
    return send_file(
        io.BytesIO(ylic_bytes),
        mimetype="application/octet-stream",
        as_attachment=True,
        download_name=filename,
    )


@licensing_bp.route("/administration/licences/<int:lic_id>/revoquer", methods=["POST"])
@super_admin_only
def revoke_issued_license(lic_id: int):
    rec = db.get_or_404(IssuedLicense, lic_id)
    if not rec.is_revoked:
        rec.is_revoked = True
        rec.revoked_at = utcnow_naive()
        record_audit_log(
            "LICENSE_REVOKE",
            f"Révocation de la licence {rec.license_serial} ({rec.beneficiary_name} / {rec.machine_fingerprint})",
            module="licensing",
            target_type="IssuedLicense",
            target_id=rec.id,
            commit=True,
        )
        flash(f"La licence {rec.license_serial} a été révoquée.", "warning")
    return redirect(url_for("licensing.manage_licenses"))


@licensing_bp.route("/administration/licences/kit-client", methods=["GET"])
@super_admin_only
def download_client_kit():
    paths: RuntimePaths = current_app.config["RUNTIME_PATHS"]
    if not is_deployment_edition(
        paths, force_client=bool(current_app.config.get("FORCE_CLIENT_EDITION", False))
    ):
        abort(403)
    version = str(current_app.config.get("APP_VERSION", "1.2.4"))
    zip_bytes = build_client_school_kit_zip(paths, version)
    record_audit_log(
        "CLIENT_KIT_EXPORT",
        f"Export du Kit Client École (sans clé privée de déploiement) v{version}",
        module="licensing",
        commit=True,
    )
    return send_file(
        io.BytesIO(zip_bytes),
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"YelenNotes-Client-Ecole-v{version}.zip",
    )


@licensing_bp.route("/administration/licences/verrouiller-client", methods=["POST"])
@super_admin_only
def lock_to_client_edition():
    paths: RuntimePaths = current_app.config["RUNTIME_PATHS"]
    try:
        convert_current_installation_to_client(
            paths,
            server_mode=str(current_app.config.get("SERVER_MODE", "monoposte")),
            fingerprint_override=current_app.config.get("TEST_MACHINE_FINGERPRINT_OVERRIDE"),
        )
    except LicenseError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("licensing.manage_licenses"))

    record_audit_log(
        "EDITION_CONVERT_TO_CLIENT",
        "Conversion de l'installation locale en Poste Client verrouillé (retrait de la clé de déploiement)",
        module="licensing",
        commit=True,
    )
    flash(
        "Cette installation est désormais verrouillée en Poste Client sous licence matérielle. "
        "La clé de déploiement Super Admin a été retirée de ce poste.",
        "success",
    )
    return redirect(url_for("licensing.manage_licenses"))


@licensing_bp.route("/administration/licences/regenerer-cles", methods=["POST"])
@super_admin_only
def regenerate_keys():
    paths: RuntimePaths = current_app.config["RUNTIME_PATHS"]
    if not is_deployment_edition(
        paths, force_client=bool(current_app.config.get("FORCE_CLIENT_EDITION", False))
    ):
        abort(403)
    new_key_id = regenerate_publisher_keypair(paths)
    record_audit_log(
        "PUBLISHER_KEY_REGENERATE",
        f"Régénération de la paire de clés maîtresse RSA-2048 ({new_key_id}) par le Super Admin",
        module="licensing",
        commit=True,
    )
    flash(
        f"Nouvelle paire de clés maîtresse RSA-2048 générée ({new_key_id}). "
        "Exportez un nouveau Kit Client École pour les prochains déploiements.",
        "success",
    )
    return redirect(url_for("licensing.manage_licenses"))

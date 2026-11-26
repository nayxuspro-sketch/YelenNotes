/* ==========================================================================
   YELEN NOTES — Frontend Architecture & Micro-Interactions (100 % Local)
   - Thème Clair / Sombre mémorisé (localStorage)
   - Sidebar réductible (desktop) & tiroir adaptatif (tablette/mobile)
   - Sous-menus déroulants par catégorie fonctionnelle
   - Recherche instantanée et tri par colonnes sur les tableaux
   - Saisie rapide au clavier, autosave CSRF et verrouillage optimiste des notes
   ========================================================================== */

(function () {
    "use strict";

    var THEME_KEY = "yn_theme_preference";
    var SIDEBAR_KEY = "yn_sidebar_collapsed";

    /* ----------------------------------------------------------------------
       1. MOTEUR DE THÈME CLAIR / SOMBRE (§12)
       ---------------------------------------------------------------------- */
    function getPreferredTheme() {
        try {
            var stored = window.localStorage.getItem(THEME_KEY);
            if (stored === "dark" || stored === "light") return stored;
        } catch (e) {}
        return "light";
    }

    function applyTheme(theme) {
        var resolved = theme === "dark" ? "dark" : "light";
        document.documentElement.setAttribute("data-theme", resolved);
        document.documentElement.setAttribute("data-bs-theme", resolved);
        try {
            window.localStorage.setItem(THEME_KEY, resolved);
        } catch (e) {}

        Array.prototype.slice.call(document.querySelectorAll("[data-yn-theme-toggle]")).forEach(function (btn) {
            var sunIcon = btn.querySelector(".yn-theme-sun");
            var moonIcon = btn.querySelector(".yn-theme-moon");
            var labelEl = btn.querySelector(".yn-theme-label");
            if (sunIcon && moonIcon) {
                sunIcon.style.display = resolved === "dark" ? "inline-flex" : "none";
                moonIcon.style.display = resolved === "dark" ? "none" : "inline-flex";
            }
            if (labelEl) {
                labelEl.textContent = resolved === "dark" ? "Mode clair" : "Mode sombre";
            }
            btn.setAttribute(
                "title",
                resolved === "dark" ? "Basculer en mode clair" : "Basculer en mode sombre"
            );
        });
    }

    function setupThemeToggle() {
        applyTheme(getPreferredTheme());
        Array.prototype.slice.call(document.querySelectorAll("[data-yn-theme-toggle]")).forEach(function (btn) {
            btn.addEventListener("click", function () {
                var current = document.documentElement.getAttribute("data-theme") || "light";
                applyTheme(current === "dark" ? "light" : "dark");
            });
        });
    }

    /* ----------------------------------------------------------------------
       2. SIDEBAR RÉDUCTIBLE, TIROIR MOBILE & SOUS-MENUS (§4.A & §10)
       ---------------------------------------------------------------------- */
    function setupSidebarAndNavigation() {
        try {
            if (window.innerWidth >= 992 && window.localStorage.getItem(SIDEBAR_KEY) === "1") {
                document.body.classList.add("yn-sidebar-collapsed");
            }
        } catch (e) {}

        var desktopToggle = document.getElementById("yn-sidebar-collapse-btn");
        if (desktopToggle) {
            desktopToggle.addEventListener("click", function () {
                var collapsed = document.body.classList.toggle("yn-sidebar-collapsed");
                try {
                    window.localStorage.setItem(SIDEBAR_KEY, collapsed ? "1" : "0");
                } catch (e) {}
            });
        }

        var mobileOpenBtn = document.getElementById("yn-mobile-menu-btn");
        var backdrop = document.getElementById("yn-sidebar-backdrop");
        if (mobileOpenBtn) {
            mobileOpenBtn.addEventListener("click", function () {
                document.body.classList.toggle("yn-mobile-nav-open");
            });
        }
        if (backdrop) {
            backdrop.addEventListener("click", function () {
                document.body.classList.remove("yn-mobile-nav-open");
            });
        }
        document.addEventListener("keydown", function (ev) {
            if (ev.key === "Escape") {
                document.body.classList.remove("yn-mobile-nav-open");
            }
        });

        // Sous-menus déroulants
        Array.prototype.slice.call(document.querySelectorAll(".yn-nav-group")).forEach(function (group) {
            var btn = group.querySelector(".yn-nav-group-btn");
            if (!btn) return;
            btn.addEventListener("click", function () {
                if (document.body.classList.contains("yn-sidebar-collapsed") && window.innerWidth >= 992) {
                    document.body.classList.remove("yn-sidebar-collapsed");
                    try {
                        window.localStorage.setItem(SIDEBAR_KEY, "0");
                    } catch (e) {}
                    group.classList.add("is-open");
                    return;
                }
                group.classList.toggle("is-open");
            });
        });
    }

    /* ----------------------------------------------------------------------
       3. TABLEAUX INTERACTIFS : RECHERCHE INSTANTANÉE & TRI COLONNES (§6)
       ---------------------------------------------------------------------- */
    function setupInteractiveTables() {
        // Recherche rapide instantanée côté client
        Array.prototype.slice.call(document.querySelectorAll("[data-yn-table-filter]")).forEach(function (input) {
            var targetSelector = input.getAttribute("data-yn-table-filter");
            var table = targetSelector ? document.querySelector(targetSelector) : null;
            if (!table) return;
            var countEl = document.querySelector('[data-yn-filter-count="' + targetSelector + '"]');

            input.addEventListener("input", function () {
                var query = (input.value || "").trim().toLowerCase();
                var rows = Array.prototype.slice.call(table.querySelectorAll("tbody > tr:not(.collapse)"));
                var visible = 0;
                rows.forEach(function (tr) {
                    if (tr.querySelector("td[colspan]")) return;
                    var text = (tr.textContent || "").toLowerCase();
                    var match = !query || text.indexOf(query) !== -1;
                    tr.style.display = match ? "" : "none";
                    if (match) visible += 1;
                });
                if (countEl) {
                    countEl.textContent = visible + " affiché(s)";
                }
            });
        });

        // Tri par clic sur les colonnes marquées .yn-sortable
        Array.prototype.slice.call(document.querySelectorAll("table")).forEach(function (table) {
            var headers = Array.prototype.slice.call(table.querySelectorAll("thead th.yn-sortable"));
            headers.forEach(function (th, colIndex) {
                th.addEventListener("click", function () {
                    var tbody = table.querySelector("tbody");
                    if (!tbody) return;
                    var rows = Array.prototype.slice.call(tbody.querySelectorAll("tr")).filter(function (r) {
                        return !r.classList.contains("collapse") && !r.querySelector("td[colspan]");
                    });
                    if (rows.length < 2) return;

                    var asc = !th.classList.contains("yn-sort-asc");
                    headers.forEach(function (h) {
                        h.classList.remove("yn-sort-asc", "yn-sort-desc");
                    });
                    th.classList.add(asc ? "yn-sort-asc" : "yn-sort-desc");

                    rows.sort(function (a, b) {
                        var cellA = a.children[colIndex] ? a.children[colIndex].textContent.trim() : "";
                        var cellB = b.children[colIndex] ? b.children[colIndex].textContent.trim() : "";
                        var numA = parseFloat(cellA.replace(/\s/g, "").replace(",", "."));
                        var numB = parseFloat(cellB.replace(/\s/g, "").replace(",", "."));
                        if (!isNaN(numA) && !isNaN(numB) && /^[-+]?[\d\s,.]+$/.test(cellA) && /^[-+]?[\d\s,.]+$/.test(cellB)) {
                            return asc ? numA - numB : numB - numA;
                        }
                        return asc
                            ? cellA.localeCompare(cellB, "fr", { sensitivity: "base" })
                            : cellB.localeCompare(cellA, "fr", { sensitivity: "base" });
                    });

                    rows.forEach(function (r) {
                        var collapseId = null;
                        var trigger = r.querySelector('[data-bs-toggle="collapse"]');
                        if (trigger) {
                            collapseId = trigger.getAttribute("data-bs-target");
                        }
                        tbody.appendChild(r);
                        if (collapseId) {
                            var detailRow = tbody.querySelector(collapseId);
                            if (detailRow) tbody.appendChild(detailRow);
                        }
                    });
                });
            });
        });
    }

    /* ----------------------------------------------------------------------
       4. MICRO-INTERACTIONS SUR SOUMISSION DE FORMULAIRES (§11)
       ---------------------------------------------------------------------- */
    function setupFormFeedback() {
        Array.prototype.slice.call(document.querySelectorAll("form[method='post'], form[method='POST']")).forEach(function (form) {
            if (form.getAttribute("action") && form.getAttribute("action").indexOf("/pdf") !== -1) return;
            form.addEventListener("submit", function () {
                var btn = form.querySelector("button[type='submit']:not([data-no-loading])");
                if (!btn || btn.dataset.submitting === "1") return;
                btn.dataset.submitting = "1";
                setTimeout(function () {
                    btn.disabled = true;
                }, 20);
            });
        });
    }

    /* ----------------------------------------------------------------------
       5. MATRICE DE SAISIE DES NOTES (CLAVIER, AUTOSAVE & CONCURRENCE)
       ---------------------------------------------------------------------- */
    function getCsrfToken() {
        var meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.getAttribute("content") : "";
    }

    function updateSaveStatusBanner(state, message) {
        var badge = document.getElementById("grade-save-status");
        if (!badge) return;
        badge.className = "badge rounded-pill px-3 py-2 ";
        if (state === "saving") {
            badge.className += "text-bg-warning";
            badge.textContent = message || "Enregistrement en cours…";
        } else if (state === "error") {
            badge.className += "text-bg-danger";
            badge.textContent = message || "Erreur de saisie à corriger";
        } else {
            badge.className += "text-bg-success";
            badge.textContent = message || "Toutes les saisies sont enregistrées";
        }
    }

    function validateGradeClientSide(input) {
        var raw = (input.value || "").trim();
        input.classList.remove("is-invalid-grade", "is-special");
        input.removeAttribute("title");

        if (raw === "") {
            return { valid: true, normalized: "" };
        }

        var upper = raw.toUpperCase();
        if (["A", "ABS", "ABS.", "ABSENT", "ABSENTE"].indexOf(upper) !== -1) {
            input.classList.add("is-special");
            return { valid: true, normalized: "ABS" };
        }
        if (["D", "DISP", "DISP.", "DISPENSE", "DISPENSÉ", "DISPENSEE", "DISPENSÉE", "NC"].indexOf(upper) !== -1) {
            if (input.dataset.allowExemption !== "1") {
                input.classList.add("is-invalid-grade");
                input.title = "La dispense n'est pas autorisée pour cette matière.";
                return { valid: false, error: input.title };
            }
            input.classList.add("is-special");
            return { valid: true, normalized: "DISP" };
        }

        var numStr = raw.replace(",", ".");
        if (!/^-?\d+(\.\d+)?$/.test(numStr)) {
            input.classList.add("is-invalid-grade");
            input.title = "Saisissez un nombre valide, ABS ou DISP.";
            return { valid: false, error: input.title };
        }

        var num = parseFloat(numStr);
        var minVal = parseFloat(input.dataset.min || "0");
        var maxVal = parseFloat(input.dataset.max || "10");
        if (num < minVal || num > maxVal) {
            input.classList.add("is-invalid-grade");
            input.title = "Hors barème : note autorisée entre " + minVal + " et " + maxVal + ".";
            return { valid: false, error: input.title };
        }
        return { valid: true, normalized: raw };
    }

    function saveCellViaApi(input, forceOverwrite) {
        var grid = document.getElementById("grades-matrix-table");
        if (!grid || !grid.dataset.apiUrl) return;

        var check = validateGradeClientSide(input);
        if (!check.valid) {
            updateSaveStatusBanner("error", check.error);
            return;
        }

        var payload = {
            enrollment_id: parseInt(input.dataset.enrollmentId, 10),
            period_id: parseInt(input.dataset.periodId, 10),
            subject_id: parseInt(input.dataset.subjectId, 10),
            value: input.value,
            version: input.dataset.version ? parseInt(input.dataset.version, 10) : null,
            force: Boolean(forceOverwrite)
        };

        input.classList.remove("is-saved");
        input.classList.add("is-saving");
        updateSaveStatusBanner("saving", "Enregistrement…");

        fetch(grid.dataset.apiUrl, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-CSRFToken": getCsrfToken(),
                "X-Requested-With": "XMLHttpRequest"
            },
            body: JSON.stringify(payload)
        })
            .then(function (resp) {
                return resp.json().then(function (data) {
                    return { status: resp.status, data: data };
                });
            })
            .then(function (res) {
                input.classList.remove("is-saving");
                if (res.status === 200 && res.data.ok) {
                    input.value = res.data.normalized_input;
                    input.dataset.version = String(res.data.version);
                    input.dataset.lastSaved = res.data.normalized_input;
                    var hiddenVer = document.getElementById(
                        "ver_" + payload.enrollment_id + "_" + payload.subject_id
                    );
                    if (hiddenVer) hiddenVer.value = String(res.data.version);

                    validateGradeClientSide(input);
                    input.classList.add("is-saved");
                    setTimeout(function () {
                        input.classList.remove("is-saved");
                    }, 1100);

                    var sumCell = document.getElementById("row-summary-" + payload.enrollment_id);
                    if (sumCell && res.data.student_summary) {
                        var s = res.data.student_summary;
                        if (s.is_complete) {
                            sumCell.innerHTML =
                                '<span class="badge text-bg-success me-1">Complet</span> ' +
                                '<strong class="yn-tabular">' + s.display_average + "</strong>";
                        } else {
                            sumCell.innerHTML =
                                '<span class="badge text-bg-warning">' +
                                (s.unranked_reason || "Incomplet") +
                                "</span>";
                        }
                    }
                    updateSaveStatusBanner("saved", "Toutes les saisies sont enregistrées");
                } else if (res.status === 409 && res.data.conflict) {
                    var overwrite = window.confirm(
                        res.data.error +
                        "\n\nCliquez sur OK pour REMPLACER par votre saisie (" + input.value + "),\n" +
                        "ou sur Annuler pour conserver la valeur du serveur (" + res.data.current_display + ")."
                    );
                    if (overwrite) {
                        saveCellViaApi(input, true);
                    } else {
                        input.value = res.data.current_display === "Non saisie" ? "" : res.data.current_display;
                        input.dataset.version = String(res.data.current_version);
                        input.dataset.lastSaved = input.value;
                        validateGradeClientSide(input);
                        updateSaveStatusBanner("saved", "Valeur synchronisée avec le serveur");
                    }
                } else {
                    input.classList.add("is-invalid-grade");
                    input.title = (res.data && res.data.error) || "Erreur lors de l'enregistrement.";
                    updateSaveStatusBanner("error", input.title);
                }
            })
            .catch(function () {
                input.classList.remove("is-saving");
                updateSaveStatusBanner("error", "Serveur momentanément injoignable — utilisez le bouton Enregistrer.");
            });
    }

    function setupGradeMatrixKeyboard() {
        var table = document.getElementById("grades-matrix-table");
        if (!table) return;

        var inputs = Array.prototype.slice.call(table.querySelectorAll(".grade-input"));
        inputs.forEach(function (inp) {
            inp.dataset.lastSaved = inp.value;
            validateGradeClientSide(inp);

            inp.addEventListener("input", function () {
                validateGradeClientSide(inp);
            });

            inp.addEventListener("change", function () {
                if (inp.value !== inp.dataset.lastSaved) {
                    saveCellViaApi(inp, false);
                }
            });

            inp.addEventListener("keydown", function (ev) {
                var rowIdx = parseInt(inp.dataset.rowIdx, 10);
                var colIdx = parseInt(inp.dataset.colIdx, 10);
                var target = null;

                if (ev.key === "Enter" || ev.key === "ArrowDown") {
                    ev.preventDefault();
                    target = table.querySelector(
                        '.grade-input[data-row-idx="' + (rowIdx + 1) + '"][data-col-idx="' + colIdx + '"]'
                    );
                } else if (ev.key === "ArrowUp") {
                    ev.preventDefault();
                    target = table.querySelector(
                        '.grade-input[data-row-idx="' + (rowIdx - 1) + '"][data-col-idx="' + colIdx + '"]'
                    );
                }
                if (target) {
                    target.focus();
                    target.select();
                }
            });
        });
    }

    function setupGradingScaleBuilder() {
        var addBtn = document.getElementById("btn-add-scale-row");
        var tbody = document.getElementById("scale-intervals-tbody");
        if (!addBtn || !tbody) return;

        function attachRemoveHandlers() {
            Array.prototype.slice.call(tbody.querySelectorAll(".btn-remove-scale-row")).forEach(function (btn) {
                btn.onclick = function () {
                    var tr = btn.closest("tr");
                    if (tr && tbody.querySelectorAll("tr").length > 1) {
                        tr.remove();
                    } else if (tr) {
                        Array.prototype.slice.call(tr.querySelectorAll("input")).forEach(function (i) {
                            i.value = "";
                        });
                    }
                };
            });
        }

        function appendRow(minV, maxV, labelV) {
            var tr = document.createElement("tr");
            tr.innerHTML =
                '<td><input type="text" name="scale_min[]" class="form-control form-control-sm" value="' + (minV || "") + '" placeholder="ex. 0"></td>' +
                '<td><input type="text" name="scale_max[]" class="form-control form-control-sm" value="' + (maxV || "") + '" placeholder="ex. 5"></td>' +
                '<td><input type="text" name="scale_label[]" class="form-control form-control-sm" value="' + (labelV || "") + '" placeholder="ex. Passable"></td>' +
                '<td class="text-end"><button type="button" class="btn btn-sm btn-outline-danger btn-remove-scale-row">Retirer</button></td>';
            tbody.appendChild(tr);
            attachRemoveHandlers();
        }

        addBtn.addEventListener("click", function () {
            appendRow("", "", "");
        });

        var preset10 = document.getElementById("btn-preset-scale-10");
        if (preset10) {
            preset10.addEventListener("click", function () {
                var scaleInput = document.getElementById("general_average_scale");
                if (scaleInput && !scaleInput.value) scaleInput.value = "10";
                tbody.innerHTML = "";
                [
                    ["0", "5", "Insuffisant"],
                    ["5", "6", "Passable"],
                    ["6", "7", "Assez bien"],
                    ["7", "8,5", "Bien"],
                    ["8,5", "10", "Très bien"]
                ].forEach(function (r) {
                    appendRow(r[0], r[1], r[2]);
                });
            });
        }

        var preset20 = document.getElementById("btn-preset-scale-20");
        if (preset20) {
            preset20.addEventListener("click", function () {
                var scaleInput = document.getElementById("general_average_scale");
                if (scaleInput && !scaleInput.value) scaleInput.value = "20";
                tbody.innerHTML = "";
                [
                    ["0", "10", "Insuffisant"],
                    ["10", "12", "Passable"],
                    ["12", "14", "Assez bien"],
                    ["14", "16", "Bien"],
                    ["16", "20", "Très bien"]
                ].forEach(function (r) {
                    appendRow(r[0], r[1], r[2]);
                });
            });
        }

        attachRemoveHandlers();
    }

    function setupSelectAllCheckboxes() {
        var master = document.getElementById("check-all-students");
        if (!master) return;
        master.addEventListener("change", function () {
            Array.prototype.slice.call(document.querySelectorAll(".student-select-checkbox")).forEach(function (cb) {
                cb.checked = master.checked;
            });
        });
    }

    document.addEventListener("DOMContentLoaded", function () {
        setupThemeToggle();
        setupSidebarAndNavigation();
        setupInteractiveTables();
        setupFormFeedback();
        setupGradeMatrixKeyboard();
        setupGradingScaleBuilder();
        setupSelectAllCheckboxes();
    });
})();

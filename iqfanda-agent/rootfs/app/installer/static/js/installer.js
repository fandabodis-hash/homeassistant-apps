"use strict";

const API = Object.freeze({
    zdraví: "/health",
    stavInstalace: "/api/installer/status",
    stavWifi: "/api/wifi/status",
    hledatWifi: "/api/wifi/scan",
    spustitInstalaci: "/api/installer/start",
    resetovatInstalaci: "/api/installer/reset",
});

const PORADI_KROKU = Object.freeze([
    "welcome",
    "account",
    "wifi",
    "summary",
    "installation",
]);

const PORADI_STAVU = Object.freeze([
    "connecting_wifi",
    "waiting_for_network",
    "registering",
    "waiting_for_heartbeat",
    "waiting_for_cloud_config",
]);

const POPISY_TYPU_OBJEKTU = Object.freeze({
    house: "Rodinný dům",
    apartment: "Byt",
    commercial: "Komerční objekt",
    industrial: "Průmyslový objekt",
    other: "Jiný objekt",
});

const dataInstalace = {
    first_name: "",
    last_name: "",
    email: "",
    account_password: "",
    site_name: "",
    site_type: "house",
    provisioning_id: "",
    ssid: "",
    password: "",
    interface: "wlan0",
    device_name: "TNG IQ FANDA",
};

let aktualniKrok = "welcome";
let casovacStavu = null;
let kontrolaStavuProbiha = false;

const prvky = {};


document.addEventListener("DOMContentLoaded", () => {
    nacistPrvky();
    zaregistrovatUdalosti();
    zobrazitKrok("welcome");
    overitPripravenost();
});


function nacistPrvky() {
    prvky.globalniUpozorneni =
        document.getElementById("global-alert");

    prvky.stavZarizeni =
        document.getElementById("device-status");

    prvky.textStavuZarizeni =
        document.getElementById("device-status-text");

    prvky.kartaPripravenosti =
        document.querySelector(".readiness-card");

    prvky.nadpisPripravenosti =
        document.getElementById("readiness-title");

    prvky.zpravaPripravenosti =
        document.getElementById("readiness-message");

    prvky.nacitaniPripravenosti =
        document.getElementById("readiness-loader");

    prvky.tlacitkoZacit =
        document.getElementById("button-start");

    prvky.formularUctu =
        document.getElementById("account-form");

    prvky.formularWifi =
        document.getElementById("wifi-form");

    prvky.tlacitkoHledatWifi =
        document.getElementById("button-scan-wifi");

    prvky.textSkenuWifi =
        document.getElementById("wifi-scan-status");

    prvky.seznamWifi =
        document.getElementById("wifi-list");

    prvky.tlacitkoInstalovat =
        document.getElementById("button-install");

    prvky.tlacitkoReset =
        document.getElementById("button-reset");

    prvky.nadpisInstalace =
        document.getElementById("installation-title");

    prvky.zpravaInstalace =
        document.getElementById("installation-message");

    prvky.hodnotaPrubehu =
        document.getElementById("progress-value");

    prvky.pruhPrubehu =
        document.getElementById("progress-bar");

    prvky.kontejnerPrubehu =
        document.querySelector(".progress-track");

    prvky.chybaInstalace =
        document.getElementById("installation-error");

    prvky.textChybyInstalace =
        document.getElementById(
            "installation-error-message"
        );

    prvky.hotovoInstalace =
        document.getElementById("installation-complete");
}


function zaregistrovatUdalosti() {
    prvky.tlacitkoZacit?.addEventListener(
        "click",
        () => zobrazitKrok("account")
    );

    prvky.formularUctu?.addEventListener(
        "submit",
        zpracovatFormularUctu
    );

    prvky.formularWifi?.addEventListener(
        "submit",
        zpracovatFormularWifi
    );

    prvky.tlacitkoHledatWifi?.addEventListener(
        "click",
        vyhledatWifiSite
    );

    prvky.tlacitkoInstalovat?.addEventListener(
        "click",
        spustitInstalaci
    );

    prvky.tlacitkoReset?.addEventListener(
        "click",
        resetovatInstalaci
    );

    document.querySelectorAll(
        '[data-action="back"]'
    ).forEach((tlacitko) => {
        tlacitko.addEventListener("click", () => {
            const cil =
                tlacitko.dataset.targetStep;

            if (cil) {
                zobrazitKrok(cil);
            }
        });
    });

    document.querySelectorAll(
        ".password-toggle"
    ).forEach((tlacitko) => {
        tlacitko.addEventListener(
            "click",
            () => prepnoutViditelnostHesla(tlacitko)
        );
    });

    document.querySelectorAll(
        ".wizard-step"
    ).forEach((tlacitko) => {
        tlacitko.addEventListener("click", () => {
            const cil =
                tlacitko.dataset.stepTarget;

            if (cil && !tlacitko.disabled) {
                zobrazitKrok(cil);
            }
        });
    });

    document.querySelectorAll(
        "input, select"
    ).forEach((pole) => {
        pole.addEventListener("input", () => {
            pole.classList.remove("is-invalid");
            skrytUpozorneni();
        });

        pole.addEventListener("change", () => {
            pole.classList.remove("is-invalid");
            skrytUpozorneni();
        });
    });
}


async function pozadavekApi(
    url,
    moznosti = {},
    timeoutMs = 15000
) {
    const ovladac = new AbortController();

    const timeout = window.setTimeout(
        () => ovladac.abort(),
        timeoutMs
    );

    try {
        const odpoved = await fetch(url, {
            cache: "no-store",
            headers: {
                Accept: "application/json",
                ...(moznosti.body
                    ? {"Content-Type": "application/json"}
                    : {}),
                ...(moznosti.headers || {}),
            },
            signal: ovladac.signal,
            ...moznosti,
        });

        let data = {};

        try {
            data = await odpoved.json();
        } catch {
            data = {};
        }

        if (!odpoved.ok) {
            const chyba = new Error(
                data.error ||
                data.message ||
                `Server vrátil stav ${odpoved.status}.`
            );

            chyba.status = odpoved.status;
            chyba.data = data;

            throw chyba;
        }

        return data;
    } catch (chyba) {
        if (chyba.name === "AbortError") {
            throw new Error(
                "Zařízení neodpovědělo v časovém limitu."
            );
        }

        throw chyba;
    } finally {
        window.clearTimeout(timeout);
    }
}


async function overitPripravenost() {
    nastavitStavZarizeni(
        "checking",
        "Kontrola zařízení"
    );

    nastavitPripravenost(
        "checking",
        "Ověřuji připravenost zařízení",
        "Probíhá kontrola Installer API a stavu zařízení."
    );

    try {
        await pozadavekApi(
            API.zdraví,
            {},
            8000
        );

        const odpovedStavu =
            await pozadavekApi(
                API.stavInstalace,
                {},
                8000
            );

        const stav =
            ziskatObjektStavu(odpovedStavu);

        if (
            stav.state &&
            stav.state !== "idle"
        ) {
            if (
                stav.state === "finished" ||
                stav.state === "error"
            ) {
                vykreslitStavInstalace(stav);
            } else {
                zobrazitKrok(
                    "installation",
                    true
                );

                spustitSledovaniStavu();
            }

            return;
        }

        nastavitStavZarizeni(
            "online",
            "Zařízení připraveno"
        );

        nastavitPripravenost(
            "ready",
            "Zařízení je připraveno",
            "Instalační služby jsou dostupné. Můžete zahájit registraci."
        );

        prvky.tlacitkoZacit.disabled = false;
    } catch (chyba) {
        nastavitStavZarizeni(
            "error",
            "Zařízení není dostupné"
        );

        nastavitPripravenost(
            "error",
            "Kontrolu se nepodařilo dokončit",
            chyba.message ||
            "Installer API není dostupné."
        );

        zobrazitUpozorneni(
            "Nepodařilo se spojit s instalační službou. " +
            "Zkontrolujte, zda je TNG IQ FANDA Agent spuštěný."
        );
    }
}


function nastavitPripravenost(
    stav,
    nadpis,
    zprava
) {
    if (!prvky.kartaPripravenosti) {
        return;
    }

    prvky.kartaPripravenosti.classList.remove(
        "is-ready",
        "is-error"
    );

    prvky.nadpisPripravenosti.textContent =
        nadpis;

    prvky.zpravaPripravenosti.textContent =
        zprava;

    if (stav === "ready") {
        prvky.kartaPripravenosti.classList.add(
            "is-ready"
        );

        prvky.nacitaniPripravenosti.hidden = true;
    } else if (stav === "error") {
        prvky.kartaPripravenosti.classList.add(
            "is-error"
        );

        prvky.nacitaniPripravenosti.hidden = true;
    } else {
        prvky.nacitaniPripravenosti.hidden = false;
    }
}


function nastavitStavZarizeni(
    stav,
    text
) {
    prvky.stavZarizeni?.classList.remove(
        "is-online",
        "is-error"
    );

    if (stav === "online") {
        prvky.stavZarizeni?.classList.add(
            "is-online"
        );
    }

    if (stav === "error") {
        prvky.stavZarizeni?.classList.add(
            "is-error"
        );
    }

    if (prvky.textStavuZarizeni) {
        prvky.textStavuZarizeni.textContent =
            text;
    }
}


function zobrazitKrok(
    krok,
    vynutit = false
) {
    if (!PORADI_KROKU.includes(krok)) {
        return;
    }

    const indexCile =
        PORADI_KROKU.indexOf(krok);

    const indexAktualni =
        PORADI_KROKU.indexOf(aktualniKrok);

    if (
        !vynutit &&
        indexCile > indexAktualni + 1
    ) {
        return;
    }

    document.querySelectorAll(
        ".wizard-screen"
    ).forEach((obrazovka) => {
        const jeAktivni =
            obrazovka.dataset.step === krok;

        obrazovka.hidden = !jeAktivni;
        obrazovka.classList.toggle(
            "is-active",
            jeAktivni
        );
    });

    document.querySelectorAll(
        ".wizard-step"
    ).forEach((tlacitko, index) => {
        const jeAktivni =
            tlacitko.dataset.stepTarget === krok;

        tlacitko.classList.toggle(
            "is-active",
            jeAktivni
        );

        tlacitko.classList.toggle(
            "is-complete",
            index < indexCile
        );

        tlacitko.disabled =
            index > indexCile ||
            krok === "installation";
    });

    aktualniKrok = krok;
    skrytUpozorneni();

    window.scrollTo({
        top: 0,
        behavior: "smooth",
    });
}


function zpracovatFormularUctu(udalost) {
    udalost.preventDefault();
    skrytUpozorneni();

    const pole = {
        first_name:
            document.getElementById("first-name"),
        last_name:
            document.getElementById("last-name"),
        email:
            document.getElementById("email"),
        account_password:
            document.getElementById(
                "account-password"
            ),
        account_password_confirm:
            document.getElementById(
                "account-password-confirm"
            ),
        site_name:
            document.getElementById("site-name"),
        site_type:
            document.getElementById("site-type"),
        provisioning_id:
            document.getElementById(
                "provisioning-id"
            ),
    };

    const povinnaPole = [
        pole.first_name,
        pole.last_name,
        pole.email,
        pole.account_password,
        pole.account_password_confirm,
        pole.site_name,
        pole.site_type,
        pole.provisioning_id,
    ];

    if (!overitPovinnaPole(povinnaPole)) {
        zobrazitUpozorneni(
            "Doplňte všechna povinná pole."
        );

        return;
    }

    const email =
        pole.email.value.trim().toLowerCase();

    if (!jePlatnyEmail(email)) {
        oznacitNeplatnePole(pole.email);

        zobrazitUpozorneni(
            "Zadejte platnou e-mailovou adresu."
        );

        return;
    }

    const heslo =
        pole.account_password.value;

    const potvrzeniHesla =
        pole.account_password_confirm.value;

    if (heslo.length < 10) {
        oznacitNeplatnePole(
            pole.account_password
        );

        zobrazitUpozorneni(
            "Heslo cloudového účtu musí mít alespoň 10 znaků."
        );

        return;
    }

    if (heslo !== potvrzeniHesla) {
        oznacitNeplatnePole(
            pole.account_password
        );

        oznacitNeplatnePole(
            pole.account_password_confirm
        );

        zobrazitUpozorneni(
            "Zadaná hesla se neshodují."
        );

        return;
    }

    dataInstalace.first_name =
        pole.first_name.value.trim();

    dataInstalace.last_name =
        pole.last_name.value.trim();

    dataInstalace.email = email;

    dataInstalace.account_password =
        heslo;

    dataInstalace.site_name =
        pole.site_name.value.trim();

    dataInstalace.site_type =
        pole.site_type.value;

    dataInstalace.provisioning_id =
        pole.provisioning_id.value.trim();

    zobrazitKrok("wifi");
}


function zpracovatFormularWifi(udalost) {
    udalost.preventDefault();
    skrytUpozorneni();

    const poleSsid =
        document.getElementById("wifi-ssid");

    const poleHeslo =
        document.getElementById(
            "wifi-password"
        );

    if (
        !overitPovinnaPole([
            poleSsid,
            poleHeslo,
        ])
    ) {
        zobrazitUpozorneni(
            "Zadejte název Wi-Fi sítě a heslo."
        );

        return;
    }

    const ssid =
        poleSsid.value.trim();

    const heslo =
        poleHeslo.value;

    if (heslo.length < 8) {
        oznacitNeplatnePole(poleHeslo);

        zobrazitUpozorneni(
            "Heslo Wi-Fi musí mít alespoň 8 znaků."
        );

        return;
    }

    dataInstalace.ssid = ssid;
    dataInstalace.password = heslo;

    naplnitSouhrn();
    zobrazitKrok("summary");
}


function overitPovinnaPole(pole) {
    let jePlatne = true;
    let prvniNeplatne = null;

    pole.forEach((prvek) => {
        if (
            !prvek ||
            !String(prvek.value || "").trim()
        ) {
            oznacitNeplatnePole(prvek);

            jePlatne = false;

            if (!prvniNeplatne) {
                prvniNeplatne = prvek;
            }
        }
    });

    prvniNeplatne?.focus();

    return jePlatne;
}


function oznacitNeplatnePole(pole) {
    if (!pole) {
        return;
    }

    pole.classList.add("is-invalid");
}


function jePlatnyEmail(email) {
    return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(
        email
    );
}


function prepnoutViditelnostHesla(
    tlacitko
) {
    const cil =
        tlacitko.dataset.passwordTarget;

    const pole =
        document.getElementById(cil);

    if (!pole) {
        return;
    }

    const zobrazit =
        pole.type === "password";

    pole.type =
        zobrazit ? "text" : "password";

    tlacitko.textContent =
        zobrazit ? "Skrýt" : "Zobrazit";

    tlacitko.setAttribute(
        "aria-label",
        zobrazit
            ? "Skrýt heslo"
            : "Zobrazit heslo"
    );
}


async function vyhledatWifiSite() {
    skrytUpozorneni();

    prvky.tlacitkoHledatWifi.disabled = true;
    prvky.tlacitkoHledatWifi.textContent =
        "Vyhledávám…";

    prvky.textSkenuWifi.textContent =
        "Probíhá vyhledávání dostupných sítí.";

    prvky.seznamWifi.innerHTML = `
        <div class="empty-state">
            <div class="spinner"></div>
            <p>Načítám dostupné Wi-Fi sítě…</p>
        </div>
    `;

    try {
        const odpoved =
            await pozadavekApi(
                API.hledatWifi,
                {
                    method: "POST",
                    body: JSON.stringify({}),
                },
                30000
            );

        if (odpoved.ok === false) {
            throw new Error(
                odpoved.error ||
                "Vyhledání Wi-Fi sítí se nezdařilo."
            );
        }

        const site =
            ziskatWifiSite(odpoved);

        vykreslitWifiSite(site);

        prvky.textSkenuWifi.textContent =
            site.length === 1
                ? "Nalezena 1 dostupná síť."
                : `Nalezeno ${site.length} dostupných sítí.`;
    } catch (chyba) {
        prvky.textSkenuWifi.textContent =
            "Vyhledání sítí se nezdařilo.";

        prvky.seznamWifi.innerHTML = `
            <div class="empty-state">
                <strong>Sítě se nepodařilo načíst</strong>
                <p>${escHtml(chyba.message)}</p>
            </div>
        `;

        zobrazitUpozorneni(
            chyba.message ||
            "Vyhledání Wi-Fi sítí se nezdařilo."
        );
    } finally {
        prvky.tlacitkoHledatWifi.disabled = false;
        prvky.tlacitkoHledatWifi.textContent =
            "Vyhledat sítě";
    }
}


function ziskatWifiSite(odpoved) {
    const mozneSeznamy = [
        odpoved.networks,
        odpoved.wifi_networks,
        odpoved.sites,
        odpoved.data?.networks,
        odpoved.data?.wifi_networks,
        odpoved.data?.sites,
        Array.isArray(odpoved.data)
            ? odpoved.data
            : null,
    ];

    const site =
        mozneSeznamy.find(Array.isArray) || [];

    const podleSsid = new Map();

    site.forEach((sit) => {
        const ssid = String(
            sit.ssid ||
            sit.name ||
            sit.network ||
            ""
        ).trim();

        if (!ssid) {
            return;
        }

        const signal = normalizovatSignal(
            sit.signal ??
            sit.strength ??
            sit.signal_strength ??
            sit.quality ??
            0
        );

        const zabezpeceni = String(
            sit.security ||
            sit.authentication ||
            sit.encryption ||
            ""
        ).trim();

        const predchozi =
            podleSsid.get(ssid);

        if (
            !predchozi ||
            signal > predchozi.signal
        ) {
            podleSsid.set(ssid, {
                ssid,
                signal,
                security: zabezpeceni,
                active: Boolean(
                    sit.active ||
                    sit.connected ||
                    sit.in_use
                ),
            });
        }
    });

    return [...podleSsid.values()]
        .sort((a, b) => {
            if (a.active !== b.active) {
                return a.active ? -1 : 1;
            }

            return b.signal - a.signal;
        });
}


function normalizovatSignal(hodnota) {
    const cislo = Number(hodnota);

    if (!Number.isFinite(cislo)) {
        return 0;
    }

    return Math.max(
        0,
        Math.min(100, Math.round(cislo))
    );
}


function vykreslitWifiSite(site) {
    if (!site.length) {
        prvky.seznamWifi.innerHTML = `
            <div class="empty-state">
                <strong>Nebyly nalezeny žádné sítě</strong>
                <p>
                    Zkontrolujte umístění zařízení
                    nebo zadejte název sítě ručně.
                </p>
            </div>
        `;

        return;
    }

    prvky.seznamWifi.innerHTML = "";

    site.forEach((sit) => {
        const tlacitko =
            document.createElement("button");

        tlacitko.type = "button";
        tlacitko.className = "wifi-network";

        const uroven =
            ziskatUrovenSignalu(sit.signal);

        const textZabezpeceni =
            sit.security ||
            "Zabezpečení neuvedeno";

        tlacitko.innerHTML = `
            <span class="wifi-network-main">
                <strong>${escHtml(sit.ssid)}</strong>
                <span>
                    ${escHtml(textZabezpeceni)}
                    ${sit.active ? " · Aktuálně připojeno" : ""}
                </span>
            </span>

            <span class="wifi-network-meta">
                <span>${sit.signal} %</span>

                <span
                    class="signal-bars"
                    data-level="${uroven}"
                    aria-label="Síla signálu ${sit.signal} procent"
                >
                    <span></span>
                    <span></span>
                    <span></span>
                    <span></span>
                </span>
            </span>
        `;

        tlacitko.addEventListener("click", () => {
            document.querySelectorAll(
                ".wifi-network"
            ).forEach((polozka) => {
                polozka.classList.remove(
                    "is-selected"
                );
            });

            tlacitko.classList.add(
                "is-selected"
            );

            const poleSsid =
                document.getElementById(
                    "wifi-ssid"
                );

            poleSsid.value = sit.ssid;
            poleSsid.classList.remove(
                "is-invalid"
            );

            document.getElementById(
                "wifi-password"
            )?.focus();
        });

        prvky.seznamWifi.appendChild(
            tlacitko
        );
    });
}


function ziskatUrovenSignalu(signal) {
    if (signal >= 75) {
        return 4;
    }

    if (signal >= 50) {
        return 3;
    }

    if (signal >= 25) {
        return 2;
    }

    return 1;
}


function naplnitSouhrn() {
    nastavText(
        "summary-customer",
        `${dataInstalace.first_name} ${dataInstalace.last_name}`
    );

    nastavText(
        "summary-email",
        dataInstalace.email
    );

    nastavText(
        "summary-site",
        dataInstalace.site_name
    );

    nastavText(
        "summary-site-type",
        POPISY_TYPU_OBJEKTU[
            dataInstalace.site_type
        ] || dataInstalace.site_type
    );

    nastavText(
        "summary-wifi",
        dataInstalace.ssid
    );
}


async function spustitInstalaci() {
    skrytUpozorneni();

    prvky.tlacitkoInstalovat.disabled = true;
    prvky.tlacitkoInstalovat.textContent =
        "Spouštím instalaci…";

    vynulovatZobrazeniInstalace();
    zobrazitKrok("installation", true);

    const payload = {
        ssid: dataInstalace.ssid,
        password: dataInstalace.password,
        interface: dataInstalace.interface,

        provisioning_id:
            dataInstalace.provisioning_id,

        first_name:
            dataInstalace.first_name,

        last_name:
            dataInstalace.last_name,

        email:
            dataInstalace.email,

        account_password:
            dataInstalace.account_password,

        site_name:
            dataInstalace.site_name,

        site_type:
            dataInstalace.site_type,

        device_name:
            dataInstalace.device_name,
    };

    try {
        const odpoved =
            await pozadavekApi(
                API.spustitInstalaci,
                {
                    method: "POST",
                    body: JSON.stringify(payload),
                },
                20000
            );

        if (odpoved.ok === false) {
            throw new Error(
                odpoved.error ||
                "Instalaci se nepodařilo spustit."
            );
        }

        const stav =
            ziskatObjektStavu(odpoved);

        vykreslitStavInstalace(stav);
        spustitSledovaniStavu();
    } catch (chyba) {
        zobrazitChybuInstalace(
            chyba.message ||
            "Instalaci se nepodařilo spustit."
        );
    } finally {
        prvky.tlacitkoInstalovat.disabled = false;
        prvky.tlacitkoInstalovat.textContent =
            "Spustit instalaci";
    }
}


function spustitSledovaniStavu() {
    zastavitSledovaniStavu();

    aktualizovatStavInstalace();

    casovacStavu = window.setInterval(
        aktualizovatStavInstalace,
        2000
    );
}


function zastavitSledovaniStavu() {
    if (casovacStavu !== null) {
        window.clearInterval(casovacStavu);
        casovacStavu = null;
    }
}


async function aktualizovatStavInstalace() {
    if (kontrolaStavuProbiha) {
        return;
    }

    kontrolaStavuProbiha = true;

    try {
        const odpoved =
            await pozadavekApi(
                API.stavInstalace,
                {},
                10000
            );

        const stav =
            ziskatObjektStavu(odpoved);

        vykreslitStavInstalace(stav);

        if (
            stav.state === "finished" ||
            stav.state === "error"
        ) {
            zastavitSledovaniStavu();
        }
    } catch (chyba) {
        prvky.zpravaInstalace.textContent =
            "Čekám na opětovné spojení se zařízením…";

        nastavitStavZarizeni(
            "checking",
            "Obnovuji spojení"
        );
    } finally {
        kontrolaStavuProbiha = false;
    }
}


function ziskatObjektStavu(odpoved) {
    if (
        odpoved &&
        typeof odpoved.setup === "object" &&
        odpoved.setup !== null
    ) {
        return odpoved.setup;
    }

    if (
        odpoved &&
        typeof odpoved.data?.setup === "object"
    ) {
        return odpoved.data.setup;
    }

    if (
        odpoved &&
        typeof odpoved.data === "object" &&
        odpoved.data !== null &&
        odpoved.data.state
    ) {
        return odpoved.data;
    }

    return odpoved || {};
}


function vykreslitStavInstalace(stav) {
    const stavNazev =
        String(stav.state || "idle");

    const prubeh =
        Math.max(
            0,
            Math.min(
                100,
                Number(stav.progress) || 0
            )
        );

    nastavitPrubeh(prubeh);

    prvky.zpravaInstalace.textContent =
        stav.message ||
        "Probíhá instalace zařízení.";

    prvky.chybaInstalace.hidden = true;
    prvky.hotovoInstalace.hidden = true;

    vykreslitFazeInstalace(
        stavNazev
    );

    if (stavNazev === "finished") {
        prvky.nadpisInstalace.textContent =
            "Instalace byla dokončena";

        prvky.zpravaInstalace.textContent =
            stav.message ||
            "Zařízení je připojeno, zaregistrováno a připraveno.";

        prvky.hotovoInstalace.hidden = false;

        nastavitStavZarizeni(
            "online",
            "Instalace dokončena"
        );

        nastavitPrubeh(100);

        doplnitVysledekInstalace(
            stav.result
        );

        return;
    }

    if (stavNazev === "error") {
        zobrazitChybuInstalace(
            stav.error ||
            stav.message ||
            "Během instalace nastala chyba."
        );

        return;
    }

    prvky.nadpisInstalace.textContent =
        "Probíhá instalace";

    nastavitStavZarizeni(
        "online",
        "Instalace probíhá"
    );
}


function vykreslitFazeInstalace(
    aktualniStav
) {
    const indexAktualni =
        PORADI_STAVU.indexOf(aktualniStav);

    document.querySelectorAll(
        ".installation-stage"
    ).forEach((faze) => {
        const stavFaze =
            faze.dataset.state;

        const indexFaze =
            PORADI_STAVU.indexOf(stavFaze);

        faze.classList.remove(
            "is-active",
            "is-complete",
            "is-error"
        );

        if (aktualniStav === "finished") {
            faze.classList.add(
                "is-complete"
            );

            return;
        }

        if (aktualniStav === "error") {
            return;
        }

        if (indexFaze < indexAktualni) {
            faze.classList.add(
                "is-complete"
            );
        } else if (
            indexFaze === indexAktualni
        ) {
            faze.classList.add(
                "is-active"
            );
        }
    });
}


function nastavitPrubeh(hodnota) {
    const celeProcento =
        Math.round(hodnota);

    prvky.hodnotaPrubehu.textContent =
        `${celeProcento} %`;

    prvky.pruhPrubehu.style.width =
        `${celeProcento}%`;

    prvky.kontejnerPrubehu?.setAttribute(
        "aria-valuenow",
        String(celeProcento)
    );
}


function zobrazitChybuInstalace(zprava) {
    zastavitSledovaniStavu();

    prvky.nadpisInstalace.textContent =
        "Instalaci se nepodařilo dokončit";

    prvky.zpravaInstalace.textContent =
        "Zařízení oznámilo chybu během instalačního procesu.";

    prvky.textChybyInstalace.textContent =
        zprava;

    prvky.chybaInstalace.hidden = false;
    prvky.hotovoInstalace.hidden = true;

    nastavitStavZarizeni(
        "error",
        "Instalace skončila chybou"
    );

    const aktivniFaze =
        document.querySelector(
            ".installation-stage.is-active"
        );

    aktivniFaze?.classList.remove(
        "is-active"
    );

    aktivniFaze?.classList.add(
        "is-error"
    );
}


function vynulovatZobrazeniInstalace() {
    zastavitSledovaniStavu();

    prvky.nadpisInstalace.textContent =
        "Probíhá instalace";

    prvky.zpravaInstalace.textContent =
        "Připravuji instalaci zařízení.";

    prvky.chybaInstalace.hidden = true;
    prvky.hotovoInstalace.hidden = true;

    document.querySelectorAll(
        ".installation-stage"
    ).forEach((faze) => {
        faze.classList.remove(
            "is-active",
            "is-complete",
            "is-error"
        );
    });

    nastavitPrubeh(0);
}


async function resetovatInstalaci() {
    prvky.tlacitkoReset.disabled = true;
    prvky.tlacitkoReset.textContent =
        "Obnovuji…";

    try {
        const odpoved =
            await pozadavekApi(
                API.resetovatInstalaci,
                {
                    method: "POST",
                    body: JSON.stringify({}),
                },
                10000
            );

        if (odpoved.ok === false) {
            throw new Error(
                odpoved.error ||
                "Reset instalace se nezdařil."
            );
        }

        zastavitSledovaniStavu();
        vynulovatZobrazeniInstalace();

        nastavitStavZarizeni(
            "online",
            "Zařízení připraveno"
        );

        zobrazitKrok(
            "welcome",
            true
        );

        prvky.tlacitkoZacit.disabled = false;
    } catch (chyba) {
        prvky.textChybyInstalace.textContent =
            chyba.message ||
            "Reset instalace se nezdařil.";
    } finally {
        prvky.tlacitkoReset.disabled = false;
        prvky.tlacitkoReset.textContent =
            "Vrátit se na začátek";
    }
}


function doplnitVysledekInstalace(
    vysledek
) {
    if (
        !vysledek ||
        typeof vysledek !== "object"
    ) {
        return;
    }

    const serioveCislo =
        vysledek.device_serial_number ||
        vysledek.serial_number;

    if (serioveCislo) {
        nastavText(
            "summary-device-serial",
            serioveCislo
        );
    }
}


function zobrazitUpozorneni(zprava) {
    if (!prvky.globalniUpozorneni) {
        return;
    }

    prvky.globalniUpozorneni.textContent =
        zprava;

    prvky.globalniUpozorneni.hidden = false;
}


function skrytUpozorneni() {
    if (!prvky.globalniUpozorneni) {
        return;
    }

    prvky.globalniUpozorneni.hidden = true;
    prvky.globalniUpozorneni.textContent = "";
}


function nastavText(id, hodnota) {
    const prvek =
        document.getElementById(id);

    if (prvek) {
        prvek.textContent =
            hodnota || "—";
    }
}


function escHtml(hodnota) {
    return String(hodnota ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}

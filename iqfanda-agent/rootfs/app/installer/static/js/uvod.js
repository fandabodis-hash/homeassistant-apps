"use strict";

document.addEventListener("DOMContentLoaded", () => {
    const telo = document.body;
    const uvodniObrazovka =
        document.getElementById("uvodni-obrazovka");
    const tlacitkoZaciname =
        document.getElementById("tlacitko-zaciname");
    const portal =
        document.querySelector(".portal-shell");

    if (!uvodniObrazovka || !tlacitkoZaciname || !portal) {
        console.error(
            "Úvodní obrazovku instalačního portálu se nepodařilo inicializovat."
        );
        return;
    }

    telo.classList.add("uvod-aktivni");

    tlacitkoZaciname.addEventListener("click", () => {
        portal.classList.remove("portal-shell-skryty");
        telo.classList.remove("uvod-aktivni");

        uvodniObrazovka.classList.add(
            "uvodni-obrazovka-skryta"
        );

        window.setTimeout(() => {
            uvodniObrazovka.hidden = true;
        }, 300);

        portal.scrollIntoView({
            behavior: "auto",
            block: "start",
        });
    });
});
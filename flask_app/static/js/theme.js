(function () {
    "use strict";

    var storageKey = "konverter-theme";
    var isFrame = window.parent && window.parent !== window;
    var hostWindow = window;
    var hostDocument = document;

    if (isFrame) {
        try {
            hostWindow = window.parent;
            hostDocument = hostWindow.document;
        } catch (error) {
            return;
        }
    }

    var root = hostDocument.documentElement;
    var storage = null;

    try {
        storage = hostWindow.localStorage;
    } catch (error) {
        storage = null;
    }

    function readStored() {
        try {
            return storage ? storage.getItem(storageKey) : null;
        } catch (error) {
            return null;
        }
    }

    function systemPrefersDark() {
        try {
            return hostWindow.matchMedia("(prefers-color-scheme: dark)").matches;
        } catch (error) {
            return false;
        }
    }

    function initialMode() {
        var stored = readStored();
        if (stored === "light" || stored === "dark") {
            return stored;
        }
        return systemPrefersDark() ? "dark" : "light";
    }

    function paint(mode) {
        root.setAttribute("data-konverter-theme", mode);
        root.style.colorScheme = mode;

        var isDark = mode === "dark";
        var label = isDark ? "Aktifkan tema terang" : "Aktifkan tema gelap";
        var buttons = hostDocument.querySelectorAll("[data-konverter-theme-toggle]");

        Array.prototype.forEach.call(buttons, function (button) {
            button.setAttribute("aria-pressed", isDark ? "true" : "false");
            button.setAttribute("aria-label", label);
            button.setAttribute("title", label);
        });
    }

    function toggle() {
        var next = root.getAttribute("data-konverter-theme") === "dark" ? "light" : "dark";

        try {
            if (storage) {
                storage.setItem(storageKey, next);
            }
        } catch (error) {
            storage = null;
        }

        paint(next);
    }

    paint(initialMode());

    if (!hostDocument.__konverterThemeBound) {
        hostDocument.__konverterThemeBound = true;

        hostDocument.addEventListener("click", function (event) {
            var target = event.target;
            var button = target && target.closest ? target.closest("[data-konverter-theme-toggle]") : null;

            if (button) {
                event.preventDefault();
                toggle();
            }
        });

        try {
            hostWindow.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", function () {
                var stored = readStored();

                if (stored !== "light" && stored !== "dark") {
                    paint(initialMode());
                }
            });
        } catch (error) {
        }
    }

    if (hostWindow.MutationObserver && hostDocument.body) {
        var observer = new hostWindow.MutationObserver(function () {
            var current = root.getAttribute("data-konverter-theme");
            paint(current === "dark" ? "dark" : "light");
        });

        observer.observe(hostDocument.body, { childList: true, subtree: true });
    }
})();

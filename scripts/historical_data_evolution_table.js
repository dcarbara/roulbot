/**
 * 🟢 Evolution Roulette Data Scraper & Collector (v3 Rescue)
 * Features:
 * 1. Scrapes existing 500 rounds.
 * 2. Monitors live game.
 * 3. PRINTS data to console if download fails.
 */
(function () {
    'use strict';

    // --- CONFIGURATION ---
    const CONFIG = {
        statsContainer: '.numbers--ca008.statistics--c4d2d', // History Panel
        liveContainer: '.recentNumbers--141d3',             // Live Strip
        valueSelector: '.value--dd5c7',
        colors: {
            'red--e421d': 'RED',
            'black--6d68f': 'BLACK',
            'green--3a325': 'GREEN'
        }
    };

    // Global variable so you can access data even if script "finishes"
    window.rouletteHistory = [];
    let lastHash = '';

    // --- HELPER: GET COLOR ---
    function getColor(node) {
        const allTags = node.getElementsByTagName('*');
        for (let el of allTags) {
            for (const [className, label] of Object.entries(CONFIG.colors)) {
                if (el.classList.contains(className)) return label;
            }
        }
        return 'UNKNOWN';
    }

    // --- PHASE 1: SCRAPE HISTORY ---
    function scrapeHistory() {
        console.log("📥 Scraping history...");
        const statsPanel = document.querySelector(CONFIG.statsContainer);

        if (!statsPanel) {
            console.warn("⚠️ History panel not found. Please open the Pie Chart/History icon in the game.");
            return;
        }

        const nodes = statsPanel.querySelectorAll('.number-container--8752e, .statistics--b39ce');
        const tempLog = [];

        nodes.forEach((node) => {
            const valEl = node.querySelector(CONFIG.valueSelector);
            if (valEl) {
                tempLog.push({
                    number: valEl.innerText.trim(),
                    color: getColor(node)
                });
            }
        });

        // Add to global log
        tempLog.forEach((data, index) => {
            window.rouletteHistory.push({
                round: index + 1,
                number: data.number,
                color: data.color,
                time: "History"
            });
        });

        console.log(`✅ Scraped ${window.rouletteHistory.length} rounds.`);

        // Set hash to avoid duplicate on first live round
        if (window.rouletteHistory.length > 0) {
            const last = window.rouletteHistory[0]; // Assuming first item is recent
            lastHash = `${last.number}-${last.color}`;
        }
    }

    // --- PHASE 2: LIVE MONITOR ---
    function processLive(node) {
        if (node.nodeType !== 1) return;
        const valEl = node.querySelector(CONFIG.valueSelector);
        if (!valEl) return;

        const number = valEl.innerText.trim();
        const color = getColor(node);

        const signature = `${number}-${color}`;
        if (signature === lastHash) return;
        lastHash = signature;

        const entry = {
            round: window.rouletteHistory.length + 1,
            number: number,
            color: color,
            time: new Date().toLocaleTimeString()
        };

        window.rouletteHistory.push(entry);
        console.log(`%c ⚡ LIVE: ${number} [${color}]`, "color: #fff; background: #333; padding: 4px;");
    }

    // --- DATA EXPORT FUNCTIONS ---

    // 1. GENERATE CSV STRING
    window.getCSV = function () {
        let csv = "Round,Number,Color,Time\n";
        window.rouletteHistory.forEach(r => {
            csv += `${r.round},${r.number},${r.color},${r.time}\n`;
        });
        return csv;
    };

    // 2. PRINT TO CONSOLE (Failsafe)
    window.printData = function () {
        console.log("👇 COPY TEXT BELOW THIS LINE 👇");
        console.log(window.getCSV());
        console.log("👆 COPY TEXT ABOVE THIS LINE 👆");
    };

    // 3. DOWNLOAD FILE
    window.downloadData = function () {
        const csv = window.getCSV();
        const blob = new Blob([csv], { type: 'text/csv' });
        const url = URL.createObjectURL(blob);

        const a = document.createElement('a');
        a.href = url;
        a.download = `Roulette_Data_${window.rouletteHistory.length}.csv`;
        document.body.appendChild(a);
        a.click();

        console.log("📥 Attempting download...");
        setTimeout(() => {
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
        }, 1000);
    };

    // --- INIT ---
    function init() {
        scrapeHistory();

        const liveContainer = document.querySelector(CONFIG.liveContainer);
        if (liveContainer) {
            new MutationObserver(ms => ms.forEach(m => m.addedNodes.forEach(processLive)))
                .observe(liveContainer, { childList: true, subtree: true });
            console.log("✅ Live Monitor Active.");
        }

        console.log(`
%c👇 HOW TO GET YOUR DATA 👇
1. Type:  downloadData()  -> Tries to save a .csv file
2. Type:  printData()     -> Prints text to console (Copy & Paste)
3. Type:  copy(getCSV())  -> Copies all data to your clipboard immediately!
        `, "font-weight: bold; color: yellow; background: #222; padding: 10px;");
    }

    init();
})();
// La finestrella di DEVIN sempre in primo piano (22/10).
//
// Riceve dalla finestra principale lo stato ("devin-float": programmi, cosa
// sta facendo, ultima foto, domanda di permesso) e le rimanda solo le scelte
// di Alessandro ("devin-float-risposta": consenti / no / ferma). Non chiama
// nessun comando dell'app: la sua capability ha solo gli eventi.
(function () {
  const EVENTO = "devin-float";
  const RISPOSTA = "devin-float-risposta";

  function disegna(doc, stato) {
    const s = stato || {};
    const titoli = (s.programmi || []).map((p) => p && p.titolo).filter(Boolean);
    doc.getElementById("programmi").textContent = titoli.length ? `usa «${titoli.join("», «")}»` : "";
    doc.getElementById("azione").textContent = s.azione || "";
    const foto = doc.getElementById("foto");
    if (typeof s.foto === "string" && s.foto.startsWith("data:image/png;base64,")) {
      foto.src = s.foto;
      foto.hidden = false;
    } else {
      foto.hidden = true;
    }
    const domanda = doc.getElementById("domanda");
    if (s.domanda && s.domanda.id) {
      domanda.dataset.id = String(s.domanda.id);
      doc.getElementById("domanda-testo").textContent = s.domanda.testo || "";
      domanda.hidden = false;
    } else {
      delete domanda.dataset.id;
      domanda.hidden = true;
    }
  }

  function avvia(win) {
    const eventi = win.__TAURI__ && win.__TAURI__.event;
    if (!eventi) return;
    const doc = win.document;
    const rispondi = (dati) => eventi.emitTo("main", RISPOSTA, dati).catch(() => {});
    const scegli = (risposta) => {
      const domanda = doc.getElementById("domanda");
      const id = domanda.dataset.id;
      if (!id) return;
      // una risposta sola: la domanda sparisce subito
      delete domanda.dataset.id;
      domanda.hidden = true;
      rispondi({ id, risposta });
    };
    doc.getElementById("consenti").addEventListener("click", () => scegli("consenti"));
    doc.getElementById("no").addEventListener("click", () => scegli("no"));
    doc.getElementById("ferma").addEventListener("click", () => {
      doc.getElementById("azione").textContent = "fermo DEVIN…";
      rispondi({ risposta: "ferma" });
    });
    eventi.listen(EVENTO, (evento) => disegna(doc, evento.payload))
      .then(() => rispondi({ risposta: "pronta" }));
  }

  globalThis.__devinFloat = { disegna, avvia };
  if (typeof window !== "undefined" && window.document) avvia(window);
})();

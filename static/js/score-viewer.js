// Partition intelligente : rend les blocs ```abc en portée SVG (ABCjs),
// audio Tone.js (Play/Stop/tempo), mode débutant, export MusicXML, impression.
// Dégradation gracieuse : sans les CDN ou si l'ABC est invalide, le code
// source reste visible avec un message d'erreur explicite.
(function() {
  'use strict';

  var TEMPOS = [60, 80, 100, 120];
  var playing = null; // viewer en cours de lecture (un seul à la fois)

  function getCookie(name) {
    var parts = document.cookie.split(';');
    for (var i = 0; i < parts.length; i++) {
      var kv = parts[i].trim().split('=');
      if (kv[0] === name) return decodeURIComponent(kv[1] || '');
    }
    return '';
  }

  function midiToFreq(midi) {
    return 440 * Math.pow(2, (midi - 69) / 12);
  }

  function nearestTempo(bpm) {
    var best = TEMPOS[0];
    TEMPOS.forEach(function(t) {
      if (Math.abs(t - bpm) < Math.abs(best - bpm)) best = t;
    });
    return best;
  }

  function fetchEvents(abc) {
    var body = new FormData();
    body.append('csrfmiddlewaretoken', getCookie('csrftoken'));
    body.append('abc', abc);
    return fetch('/partition/events/', {
      method: 'POST', body: body,
      headers: { 'X-CSRFToken': getCookie('csrftoken') },
    }).then(function(resp) {
      return resp.json().then(function(data) {
        if (!resp.ok) throw new Error(data.error || ('HTTP ' + resp.status));
        return data;
      });
    });
  }

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function showError(box, message, sourcePre) {
    box.innerHTML = '';
    var err = el('div', 'score-error', 'Partition illisible : ' + message + ' ');
    var link = el('a', '', 'Voir des exemples valides');
    link.href = '/partition/demo/';
    err.appendChild(link);
    box.appendChild(err);
    if (sourcePre) sourcePre.style.display = '';
  }

  function stopPlayback() {
    if (!playing) return;
    try {
      var transport = window.Tone.getTransport();
      transport.stop();
      transport.cancel();
    } catch (e) { /* audio déjà coupé */ }
    playing.btn.textContent = '▶ Lire';
    playing.btn.setAttribute('aria-label', 'Lire la partition');
    playing = null;
  }

  function buildViewer(codeEl, raw, data) {
    var source = codeEl.closest('.codeblock') || codeEl.closest('pre');
    var box = el('div', 'score-viewer');
    box.setAttribute('role', 'region');
    box.setAttribute('aria-label', 'Partition : ' + data.title);

    var head = el('div', 'score-head');
    head.appendChild(el('span', 'score-title', data.title));
    var btnCode = el('button', 'score-btn', 'Code ABC');
    btnCode.type = 'button';
    btnCode.setAttribute('aria-label', 'Afficher ou masquer le code ABC source');
    btnCode.onclick = function() {
      source.style.display = source.style.display === 'none' ? '' : 'none';
    };
    head.appendChild(btnCode);

    var tempoLabel = el('label', 'score-tempo', 'Tempo ');
    var tempoSel = el('select', '');
    tempoSel.setAttribute('aria-label', 'Tempo en battements par minute');
    TEMPOS.forEach(function(t) {
      var opt = document.createElement('option');
      opt.value = String(t);
      opt.textContent = String(t) + ' BPM';
      if (t === nearestTempo(data.tempo_bpm)) opt.selected = true;
      tempoSel.appendChild(opt);
    });
    tempoSel.onchange = stopPlayback;
    tempoLabel.appendChild(tempoSel);
    head.appendChild(tempoLabel);

    var btnPlay = el('button', 'score-btn score-play', '▶ Lire');
    btnPlay.type = 'button';
    btnPlay.setAttribute('aria-label', 'Lire la partition');
    head.appendChild(btnPlay);

    var btnBegin = el('button', 'score-btn', 'Notes');
    btnBegin.type = 'button';
    btnBegin.setAttribute('aria-pressed', 'false');
    btnBegin.setAttribute('aria-label', 'Afficher les noms des notes (mode débutant)');
    btnBegin.title = 'Mode débutant : noms des notes';
    head.appendChild(btnBegin);

    var form = document.createElement('form');
    form.method = 'post';
    form.action = '/partition/musicxml/';
    var csrf = document.createElement('input');
    csrf.type = 'hidden'; csrf.name = 'csrfmiddlewaretoken'; csrf.value = getCookie('csrftoken');
    var inAbc = document.createElement('input');
    inAbc.type = 'hidden'; inAbc.name = 'abc'; inAbc.value = raw;
    var inTitle = document.createElement('input');
    inTitle.type = 'hidden'; inTitle.name = 'title'; inTitle.value = data.title;
    var btnXml = el('button', 'score-btn', 'MusicXML');
    btnXml.type = 'submit';
    btnXml.setAttribute('aria-label', 'Télécharger la partition en MusicXML');
    form.appendChild(csrf); form.appendChild(inAbc); form.appendChild(inTitle); form.appendChild(btnXml);
    head.appendChild(form);

    var btnPrint = el('button', 'score-btn', 'Imprimer');
    btnPrint.type = 'button';
    btnPrint.setAttribute('aria-label', 'Imprimer la partition');
    head.appendChild(btnPrint);
    box.appendChild(head);

    var staff = el('div', 'score-staff');
    box.appendChild(staff);
    try {
      window.ABCJS.renderAbc(staff, raw, { responsive: 'resize' });
    } catch (e) {
      showError(box, 'rendu impossible (' + (e.message || e) + ').', source);
      return;
    }
    if (!staff.querySelector('svg')) {
      showError(box, 'aucune portée générée. Vérifiez les en-têtes X:, M:, L:, K: et les notes.', source);
      return;
    }

    var names = el('div', 'score-names', data.events.map(function(e) { return e.name; }).join(' – '));
    names.style.display = 'none';
    names.setAttribute('aria-label', 'Noms des notes');
    box.appendChild(names);
    btnBegin.onclick = function() {
      var show = names.style.display === 'none';
      names.style.display = show ? '' : 'none';
      btnBegin.setAttribute('aria-pressed', show ? 'true' : 'false');
    };

    var state = { btn: btnPlay, data: data, box: box };
    btnPlay.onclick = function() {
      if (playing && playing.box === box) { stopPlayback(); return; }
      stopPlayback();
      var play = function() {
        var transport = window.Tone.getTransport();
        transport.cancel();
        var synth = new window.Tone.Synth().toDestination();
        var bpm = parseInt(tempoSel.value, 10) || 100;
        var secPerBeat = 60 / bpm;
        data.events.forEach(function(e) {
          var freq = midiToFreq(e.midi);
          var at = e.start * secPerBeat;
          var dur = Math.min(e.dur * secPerBeat, 4);
          transport.schedule(function(time) {
            synth.triggerAttackRelease(freq, dur, time);
          }, at);
        });
        transport.start();
        btnPlay.textContent = '⏹ Stop';
        btnPlay.setAttribute('aria-label', 'Arrêter la lecture');
        playing = state;
      };
      window.Tone.start().then(play, function() {
        showError(box, "audio impossible (navigateur sans Web Audio ?). La portée reste lisible.", null);
      });
    };

    btnPrint.onclick = function() {
      var w = window.open('', '_blank');
      if (!w) return;
      w.document.write(
        '<html><head><title>' + data.title.replace(/</g, '&lt;') + '</title></head><body>' +
        '<h1>' + data.title.replace(/</g, '&lt;') + '</h1>' + staff.innerHTML + '</body></html>'
      );
      w.document.close();
      w.focus();
      w.print();
    };

    source.parentNode.insertBefore(box, source.nextSibling);
    source.style.display = 'none';
  }

  function processBlock(codeEl) {
    if (codeEl.dataset.scoreDone) return;
    codeEl.dataset.scoreDone = '1';
    if (!window.ABCJS || !window.Tone) return; // CDN indisponible : code lisible tel quel
    var raw = codeEl.textContent || '';
    var source = codeEl.closest('.codeblock') || codeEl.closest('pre');
    var box = el('div', 'score-viewer');
    source.parentNode.insertBefore(box, source.nextSibling);
    fetchEvents(raw).then(function(data) {
      box.remove();
      buildViewer(codeEl, raw, data);
    }, function(err) {
      showError(box, err.message, source);
    });
  }

  function renderScores(root) {
    (root || document).querySelectorAll('code.language-abc').forEach(processBlock);
  }

  window.renderScores = renderScores;
  document.addEventListener('DOMContentLoaded', function() { renderScores(document); });
})();

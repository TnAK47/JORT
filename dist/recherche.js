/* Recherche commune aux annonces et aux entités : aucun rapprochement d'identité. */
(() => {
  'use strict';
  const normalize = value => String(value ?? '').normalize('NFD').replace(/\p{M}/gu, '').toLocaleLowerCase('fr').replace(/[^\p{L}\p{N}]+/gu, ' ').trim();
  function prepare(value, exact = false, fieldsNormalized = false) {
    const query = normalize(value), words = [...new Set(query.split(' ').filter(Boolean))];
    return fields => {
      if (!query) return { score:1, matched:0, total:0, kind:'', label:'' };
      let best = { score:0, matched:0, total:words.length, kind:'', label:'' };
      for (const field of fields) {
        const text = fieldsNormalized ? field : normalize(field), bounded = ` ${text} `;
        const phrase = bounded.includes(` ${query} `);
        if (exact && !phrase) continue;
        let count = words.reduce((n, word) => n + Number(bounded.includes(` ${word} `)), 0);
        // Une recherche simple conserve la recherche partielle historique.
        if (words.length === 1 && !exact && !count && text.includes(words[0])) count = 1;
        if (!count) continue;
        const all = count === words.length;
        let cursor = 0;
        const ordered = all && words.every(word => { const at = bounded.indexOf(` ${word} `, cursor); if (at < 0) return false; cursor = at + word.length + 1; return true; });
        const score = count * 100 + (phrase ? 30 : ordered ? 20 : all ? 10 : 0);
        if (score > best.score) {
          const kind = all ? (phrase || ordered ? 'ordered' : 'unordered') : count > 1 ? 'partial-many' : 'partial-one';
          const label = phrase ? 'Expression exacte' : ordered ? 'Tous les mots dans l’ordre' : all ? 'Tous les mots, ordre différent' : `${count}/${words.length} mots retrouvés`;
          best = { score, matched:count, total:words.length, kind, label };
        }
      }
      return best;
    };
  }
  globalThis.JORT_SEARCH = { prepare, normalize };
})();

'use strict';

function createTriageMap({api, cases, openFax, onUpdate, modelAvailable}) {
  const get = id => document.getElementById(id);
  const priorities = {
    critical: 'Critical', high: 'High', medium: 'Medium', low: 'Low', needs_review: 'Needs review',
  };
  const scopes = [...document.querySelectorAll('[data-triage-scope]')];
  const examples = [...document.querySelectorAll('[data-clue]')];
  const input = get('triage-query');
  const stats = get('triage-stats');
  const lanes = get('triage-lanes');
  let scope = 'packet';
  let clues = [];
  let classification = null;
  let matches = null;
  let busy = false;
  let problem = '';
  let timing = null;
  let lastRequest = null;

  function add(parent, tag, text, className) {
    const child = document.createElement(tag);
    if (text !== undefined) child.textContent = text;
    if (className) child.className = className;
    parent.append(child);
    return child;
  }

  function percent(value) {
    return Math.round(100 * value) + '%';
  }

  function priority(id) {
    return classification?.rows.find(row => row.id === id)?.route;
  }

  function pageLabel(source) {
    if (source?.endsWith('-SCAN')) return 'Scanned page 2';
    const page = source?.match(/-P(\d+)$/);
    return page ? 'Page ' + page[1] : 'No signed report found';
  }

  function renderStats() {
    stats.replaceChildren();
    if (busy) add(stats, 'strong', clues.length ? 'Scoring all referrals…' : 'Classifying the inbox…');
    else if (problem) add(stats, 'strong', problem, 'triage-error');
    else if (matches) {
      const count = matches.rows.filter(row => row.matched).length;
      add(stats, 'strong', count + ' of ' + cases().length + ' match every clue');
    } else if (classification) {
      const rows = classification.rows;
      const agree = rows.filter(row => row.agrees_with_local).length;
      add(stats, 'strong', rows.length + ' classified · ' + agree + '/' + rows.length + ' pass source checks');
    } else {
      add(stats, 'span', modelAvailable ? 'Run a live classification to see the queue.' : 'Configure SubQ on the server to classify the inbox.');
    }
    if (!busy && !problem && lastRequest) {
      const r = lastRequest;
      const calls = r.calls ?? 1;
      const evidence = r.context === 'search_index'
        ? r.pages + ' indexed pages'
        : r.pages + ' pages · ' + r.images + ' ' + (r.images === 1 ? 'scan' : 'scans');
      const metrics = [
        calls + (calls === 1 ? ' Snap call · ' : ' Snap calls · ') + r.questions + ' decisions',
        evidence,
        r.input_tokens.toLocaleString() + ' input tokens',
        (r.elapsed_ms / 1000).toFixed(2) + ' s Snap · ' + (timing / 1000).toFixed(2) + ' s total',
      ];
      if (r.output_tokens !== null) metrics.splice(3, 0, r.output_tokens + ' output tokens');
      if (r.verified_clues?.length) {
        metrics.push(r.verified_clues.length === 1 ? '1 clue checked against source' :
          r.verified_clues.length + ' clues checked against source');
      }
      for (const metric of metrics) add(stats, 'span', metric, 'triage-metric');
    }
  }

  function renderClues() {
    const list = get('triage-clues');
    list.replaceChildren();
    list.hidden = clues.length === 0;
    if (!clues.length) return;
    add(list, 'span', 'Match all');
    clues.forEach((clue, index) => {
      const button = add(list, 'button', clue + ' ×');
      button.type = 'button';
      button.disabled = busy;
      button.setAttribute('aria-label', 'Remove clue ' + clue);
      button.addEventListener('click', () => changeClues(clues.filter((_, i) => i !== index)));
    });
    const clear = add(list, 'button', 'Clear clues');
    clear.type = 'button';
    clear.disabled = busy;
    clear.addEventListener('click', () => changeClues([]));
  }

  function renderLanes() {
    lanes.replaceChildren();
    const rowById = new Map((matches?.rows || []).map(row => [row.id, row]));
    const records = new Map(cases().map(item => [item.id, item]));
    for (const [route, label] of Object.entries(priorities)) {
      const lane = add(lanes, 'section', undefined, 'triage-lane');
      lane.dataset.route = route;
      const head = add(lane, 'div', undefined, 'triage-lane-head');
      add(head, 'h2', label);
      const rows = (classification?.rows || []).filter(row => row.route === route);
      const matched = rows.filter(row => rowById.get(row.id)?.matched).length;
      const count = add(head, 'span', matches ? matched + ' / ' + rows.length : classification ? String(rows.length) : '–');
      if (matches) count.setAttribute('aria-label', matched + ' matching out of ' + rows.length);
      rows.sort((a, b) => {
        const aMatch = rowById.get(a.id);
        const bMatch = rowById.get(b.id);
        return Number(Boolean(bMatch?.matched)) - Number(Boolean(aMatch?.matched)) ||
          (bMatch?.score || 0) - (aMatch?.score || 0);
      });
      if (!rows.length) add(lane, 'p', classification ? 'No referrals' : 'Awaiting results', 'triage-empty');
      const cards = add(lane, 'div', undefined, 'triage-lane-cards');
      cards.setAttribute('role', 'group');
      cards.setAttribute('aria-label', label + ' referrals');
      for (const row of rows) {
        const item = records.get(row.id);
        if (!item) continue;
        const match = rowById.get(row.id);
        const card = add(cards, 'button', undefined, 'triage-case');
        card.type = 'button';
        card.classList.toggle('match', Boolean(match?.matched));
        card.classList.toggle('nonmatch', Boolean(match && !match.matched));
        add(card, 'strong', item.clinic);
        const images = item.attachments?.length || 0;
        const imageCount = images ? ' · ' + images + (images === 1 ? ' image' : ' images') : '';
        add(card, 'span', row.id + ' · ' + pageLabel(row.source) + imageCount);
        if (!row.agrees_with_local) {
          add(card, 'span', 'Disagrees with local checks');
        }
        if (match?.model_matched && match.verified_match === false) {
          add(card, 'span', 'Query match blocked by source check');
        }
        add(card, 'span', match ? percent(match.score) + ' query match' :
          percent(row.probabilities[row.route]) + ' priority score', 'triage-score');
        if (match) {
          const track = add(card, 'span', undefined, 'triage-meter');
          track.setAttribute('aria-hidden', 'true');
          add(track, 'i').style.width = percent(match.score);
        }
        card.addEventListener('click', () => openFax(item, match?.matched ? match.source : row.source));
      }
      if (cards.scrollHeight > cards.clientHeight) cards.tabIndex = 0;
    }
  }

  function render() {
    for (const button of scopes) {
      button.setAttribute('aria-pressed', String(button.dataset.triageScope === scope));
      button.disabled = busy || !modelAvailable;
    }
    for (const button of examples) button.disabled = busy || !classification;
    get('classify-inbox').disabled = busy || !modelAvailable;
    input.disabled = busy || !classification;
    get('add-clue').disabled = busy || !classification;
    get('triage-trace').hidden = !lastRequest;
    get('triage-trace-json').textContent = lastRequest ? JSON.stringify(lastRequest, null, 2) : '';
    renderClues();
    renderStats();
    renderLanes();
  }

  async function run(search) {
    if (busy || !modelAvailable) return;
    busy = true;
    problem = '';
    render();
    const started = performance.now();
    try {
      const result = await api('/triage', {scope, clues: search ? clues : []});
      timing = Math.round(performance.now() - started);
      lastRequest = result.request;
      if (!result.available) throw new Error(result.message);
      if (search) matches = result;
      else {
        classification = {...result, client_ms: timing};
        onUpdate();
      }
    } catch (error) {
      problem = error.message || 'Snap did not return a result.';
      if (search) matches = null;
    } finally {
      busy = false;
      render();
    }
  }

  async function changeClues(next) {
    if (busy || !classification) return;
    clues = next;
    matches = null;
    problem = '';
    lastRequest = classification.request;
    timing = classification.client_ms;
    if (clues.length) await run(true);
    else render();
  }

  function addClue(value, refocus = false) {
    const clue = value.trim().replace(/\s+/g, ' ');
    if (!clue || clues.some(existing => existing.toLowerCase() === clue.toLowerCase())) return;
    if (clues.length >= 4) {
      problem = 'Remove a clue before adding another. The map accepts four clues.';
      renderStats();
      return;
    }
    input.value = '';
    void changeClues([...clues, clue]).then(() => {
      if (refocus && document.activeElement === document.body) input.focus({preventScroll: true});
    });
  }

  async function classify() {
    if (busy) return;
    clues = [];
    matches = null;
    classification = null;
    lastRequest = null;
    input.value = '';
    onUpdate();
    await run(false);
  }

  get('triage-query-form').addEventListener('submit', event => {
    event.preventDefault();
    addClue(input.value, true);
  });
  input.addEventListener('keydown', event => {
    if (event.key === 'Escape') {
      event.preventDefault();
      input.value = '';
      void changeClues([]);
    }
  });
  for (const button of examples) button.addEventListener('click', () => addClue(button.dataset.clue));
  for (const button of scopes) {
    button.addEventListener('click', () => {
      if (scope === button.dataset.triageScope) return;
      scope = button.dataset.triageScope;
      classify();
    });
  }
  get('classify-inbox').addEventListener('click', classify);
  render();
  return {classify, priority, reset: () => { scope = 'packet'; return classify(); }};
}

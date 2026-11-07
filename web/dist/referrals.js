'use strict';

const el = id => document.getElementById(id);
const controls = [...document.querySelectorAll('[data-scope]')];
const tabs = [...document.querySelectorAll('[data-tab]')];
const routes = {critical: 'Critical', high: 'High', medium: 'Medium', low: 'Low', needs_review: 'Needs review'};
const labLabels = {result: 'Final lab result', order: 'Lab order only', missing: 'No lab document supplied'};
const decisions = new Map();
let inbox;
let current;
let documentData;
let activeTab = 'pages';
let pending = false;

function append(parent, tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  parent.append(node);
  return node;
}

async function api(action = '', data) {
  const response = await fetch('/api/referrals' + action, data === undefined ? {} : {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(data),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Request failed.');
  return result;
}

function renderQueue() {
  const list = el('queue');
  list.replaceChildren();
  for (const item of inbox.cases) {
    const button = append(list, 'button', undefined, 'queue-item');
    button.type = 'button';
    button.setAttribute('aria-current', String(item.id === current?.id));
    append(button, 'strong', item.clinic);
    const label = item.review ? routes[item.review.route] : item.scanned ? 'Scan attached' : 'Unreviewed';
    append(button, 'small', item.id + ' · ' + item.received + ' · ' + label);
    button.addEventListener('click', () => selectCase(item));
  }
  const done = inbox.cases.filter(item => item.review).length;
  el('progress').textContent = done === inbox.cases.length ? '5 of 5 · Inbox cleared' : done + ' of ' + inbox.cases.length + ' complete';
  el('progress-bar').value = done;
  const calendar = el('calendar');
  calendar.replaceChildren();
  const reviewed = inbox.cases.filter(item => item.review);
  if (!reviewed.length) append(calendar, 'p', 'Confirm a reviewer disposition to see it here.', 'small');
  for (const item of reviewed) {
    const line = append(calendar, 'div', undefined, 'calendar-line');
    append(line, 'strong', item.id + ' · ' + routes[item.review.route]);
    append(line, 'div', item.review.slot || item.review.status, 'small');
  }
}

function renderDocument() {
  const panel = el('document');
  panel.replaceChildren();
  for (const tab of tabs) tab.setAttribute('aria-pressed', String(tab.dataset.tab === activeTab));
  if (activeTab === 'scan') {
    const figure = append(panel, 'figure');
    const image = append(figure, 'img');
    image.src = '/referral-assets/R101-addendum.png';
    image.alt = 'Fictional signed radiology addendum with a critical routing instruction';
    append(figure, 'figcaption', 'Scanned signed addendum · PDF page 2');
    if (documentData?.ocr) {
      const details = append(panel, 'details');
      append(details, 'summary', 'Local OCR transcript');
      append(details, 'pre', documentData.ocr);
    } else if (documentData?.ocr_error) {
      append(panel, 'p', documentData.ocr_error, 'small');
    }
    return;
  }
  if (activeTab === 'mri') {
    if (inbox.mri_available) {
      const figure = append(panel, 'figure');
      const image = append(figure, 'img', undefined, 'mri');
      image.src = '/referral-assets/brainweb-local.png';
      image.alt = 'Axial slice from the BrainWeb simulated normal brain MRI';
      append(figure, 'figcaption', 'BrainWeb simulated T1 MRI phantom · illustrative attachment · excluded from routing');
    } else {
      append(panel, 'p', 'The local MRI teaching slice has not been installed.');
      const link = append(panel, 'a', 'View the BrainWeb simulated MRI source ↗');
      link.href = 'https://www.mcgill.ca/bic/software/brainweb-mri-simulator';
    }
    return;
  }
  if (!documentData) {
    append(panel, 'p', 'Extracting PDF text…', 'small');
    return;
  }
  documentData.pages.forEach((page, index) => {
    const details = append(panel, 'details');
    details.open = index === 0;
    append(details, 'summary', 'PDF page ' + (index + 1));
    append(details, 'pre', page);
    if (index === 1 && current.scanned) {
      const button = append(details, 'button', 'Open scanned addendum');
      button.type = 'button';
      button.addEventListener('click', () => { activeTab = 'scan'; renderDocument(); });
    }
  });
}

function renderComparisons() {
  const panel = el('comparisons');
  panel.replaceChildren();
  const results = decisions.get(current.id) || {};
  for (const scope of ['letter', 'packet']) {
    const result = results[scope];
    if (!result) continue;
    const card = append(panel, 'div', undefined, 'result');
    append(card, 'span', scope === 'letter' ? 'LETTER ONLY' : 'FULL PACKET', 'eyebrow');
    if (!result.available) {
      append(card, 'p', result.message, 'metric');
      continue;
    }
    append(card, 'strong', routes[result.route], result.route);
    const evidence = result.source === 'SCAN' ? 'Scanned addendum, PDF page 2'
      : result.source === 'NONE' ? 'No signed instruction found'
      : 'PDF page ' + result.source.slice(1);
    append(card, 'div', evidence, 'metric');
    append(card, 'div', labLabels[result.lab], 'metric');
    append(card, 'div', result.pages + ' page(s) · ' + result.images + ' image(s) · ' + result.input_tokens.toLocaleString() + ' input tokens', 'small');
    const probabilities = append(card, 'details');
    append(probabilities, 'summary', 'Model scores');
    const list = append(probabilities, 'dl');
    for (const [route, probability] of Object.entries(result.probabilities)) {
      append(list, 'dt', routes[route]);
      append(list, 'dd', (probability * 100).toFixed(1) + '%');
    }
  }
}

function renderReview() {
  const outcome = el('outcome');
  const review = current.review;
  el('review').hidden = Boolean(review);
  outcome.hidden = !review;
  outcome.replaceChildren();
  if (review) {
    append(outcome, 'strong', routes[review.route] + ' · ' + review.status, review.route);
    if (review.slot) append(outcome, 'div', review.slot);
    if (review.reason) append(outcome, 'div', review.reason, 'small');
  }
}

async function selectCase(item) {
  current = item;
  documentData = null;
  activeTab = 'pages';
  el('case-id').textContent = item.id + ' · RECEIVED ' + item.received;
  el('case-title').textContent = item.clinic;
  el('open-pdf').href = '/referral-assets/' + item.id + '.pdf';
  el('open-pdf').hidden = false;
  tabs.find(tab => tab.dataset.tab === 'scan').hidden = !item.scanned;
  el('review').reset();
  el('error').textContent = '';
  renderQueue();
  renderDocument();
  renderComparisons();
  renderReview();
  try {
    const result = await api('/document', {id: item.id});
    if (current === item) {
      documentData = result;
      renderDocument();
    }
  } catch (error) {
    if (current === item) el('document').textContent = error.message;
  }
}

function setBusy(value) {
  pending = value;
  for (const button of controls) button.disabled = value;
  el('reset').disabled = value;
  el('review').querySelector('button').disabled = value;
}

for (const tab of tabs) {
  tab.addEventListener('click', () => { activeTab = tab.dataset.tab; renderDocument(); });
}
for (const button of controls) {
  button.addEventListener('click', async () => {
    if (pending || !current) return;
    const item = current;
    const scope = button.dataset.scope;
    setBusy(true);
    el('error').textContent = '';
    const label = button.textContent;
    button.textContent = 'Asking Snap…';
    try {
      const result = await api('/assess', {id: item.id, scope});
      decisions.set(item.id, {...decisions.get(item.id), [scope]: result});
      if (current === item) {
        renderComparisons();
        if (scope === 'packet' && result.available && !item.review && !el('route').value) {
          el('route').value = result.route;
        }
      }
    } catch (error) {
      if (current === item) el('error').textContent = error.message;
    } finally {
      button.textContent = label;
      setBusy(false);
    }
  });
}

el('review').addEventListener('submit', async event => {
  event.preventDefault();
  if (pending || !current) return;
  const item = current;
  setBusy(true);
  el('error').textContent = '';
  try {
    item.review = await api('/review', {
      id: item.id,
      route: el('route').value,
      reason: el('reason').value,
      confirmed: el('confirmed').checked,
    });
    renderQueue();
    if (current === item) renderReview();
  } catch (error) {
    if (current === item) el('error').textContent = error.message;
  } finally {
    setBusy(false);
  }
});

el('reset').addEventListener('click', async () => {
  if (pending) return;
  setBusy(true);
  try {
    inbox = await api('/reset', {});
    decisions.clear();
    await selectCase(inbox.cases[0]);
  } catch (error) {
    el('error').textContent = error.message;
  } finally {
    setBusy(false);
  }
});

async function start() {
  inbox = await api();
  el('live').textContent = inbox.model_available ? 'LIVE AVAILABLE' : 'MANUAL REVIEW';
  el('policy').textContent = inbox.policy;
  await selectCase(inbox.cases[0]);
}
start().catch(error => { el('error').textContent = error.message; });

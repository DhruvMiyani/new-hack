'use strict';

const el = id => document.getElementById(id);
const controls = [...document.querySelectorAll('[data-scope]')];
const tabs = [...document.querySelectorAll('[data-tab]')];
const routes = {
  critical: 'Critical', high: 'High', medium: 'Medium', low: 'Low',
  needs_review: 'Needs review', intake_coordination: 'Intake coordination',
};
const acceptanceLabels = {
  eligible: 'Eligible for scheduling', outside_area: 'Outside service area',
  manual_review: 'Verify county',
};
const acceptanceActions = {
  continue_review: 'Next step: review the signed routing instruction.',
  intake_coordination: 'Next step: intake coordination. No specialist slot.',
};
const labLabels = {result: 'Final lab result', order: 'Lab order only', missing: 'No lab document supplied'};
const decisions = new Map();
const museResults = new Map();
let inbox;
let current;
let documentData;
let documentError;
let activeTab = 'fax';
let activeFaxPage = 1;
let pending = false;
let musePending = null;
let triageMap;

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
  const queueScroll = list.scrollTop;
  list.replaceChildren();
  const query = el('queue-search').value.trim().toLowerCase();
  const cases = inbox.cases.filter(item => {
    const fax = item.fax || {};
    return [item.id, fax.id, item.clinic, fax.sender_name, fax.sender_number, item.patient_county]
      .some(value => value?.toLowerCase().includes(query));
  });
  el('fax-count').textContent = cases.length + ' / ' + inbox.cases.length;
  if (!cases.length) append(list, 'p', 'No matching faxes.', 'list-foot');
  for (const item of cases) {
    const fax = item.fax || {};
    const button = append(list, 'button', undefined, 'queue-item');
    button.type = 'button';
    button.dataset.case = item.id;
    button.setAttribute('aria-current', String(item.id === current?.id));
    const sender = append(button, 'span');
    append(sender, 'strong', fax.sender_name || item.clinic);
    append(sender, 'small', fax.sender_number || 'Sender number unavailable');
    const pageCount = fax.page_count ? ' · ' + fax.page_count + ' pg' : '';
    const imageCount = item.attachments?.length || 0;
    const images = imageCount ? ' · ' + imageCount + (imageCount === 1 ? ' image' : ' images') : '';
    append(sender, 'small', (fax.id || item.id) + pageCount + images);
    const status = append(button, 'span');
    append(status, 'span', fax.received_at || item.received, 'queue-time');
    const priority = triageMap?.priority(item.id);
    const label = item.review ? routes[item.review.route] : priority ? 'Snap · ' + routes[priority] : 'New';
    append(status, 'span', label, 'tag queue-state');
    button.addEventListener('click', () => {
      selectCase(item);
      list.querySelector('[data-case="' + CSS.escape(item.id) + '"]')?.focus({preventScroll: true});
    });
  }
  list.scrollTop = queueScroll;
  const done = inbox.cases.filter(item => item.review).length;
  el('progress').textContent = done === inbox.cases.length ? 'Inbox cleared' : done + ' of ' + inbox.cases.length + ' reviewed';
  el('progress-bar').max = inbox.cases.length;
  el('progress-bar').value = done;
  const calendar = el('calendar');
  calendar.replaceChildren();
  const reviewed = inbox.cases.filter(item => item.review);
  if (!reviewed.length) append(calendar, 'p', 'Appointments, escalations, and intake holds will appear here.', 'small');
  for (const item of reviewed) {
    const line = append(calendar, 'div', undefined, 'calendar-line');
    append(line, 'strong', item.id + ' · ' + routes[item.review.route]);
    append(line, 'div', item.review.slot || item.review.status, 'small');
  }
}

function renderFaxMetadata() {
  const fax = current.fax || {};
  const panel = el('fax-meta');
  panel.replaceChildren();
  for (const [label, value] of [
    ['Source', (fax.channel || 'eFax') + ' · simulated'],
    ['Sender fax', fax.sender_number || 'Unavailable'],
    ['Received', fax.received_at || current.received],
    ['Fax ID', fax.id || current.id],
    ['Fax pages', fax.page_count || '—'],
    ['Patient county', current.patient_county || 'Not documented'],
  ]) {
    const field = append(panel, 'div');
    append(field, 'dt', label);
    append(field, 'dd', String(value));
  }
}

function renderRulesBook() {
  const panel = el('rules-book');
  panel.replaceChildren();
  const book = inbox.rules_book;
  if (!book) {
    append(panel, 'p', 'The local rules book is unavailable.');
    return;
  }
  append(panel, 'p', book.name + ' · ' + book.version);
  append(panel, 'p', 'Specialist: ' + book.specialist);
  append(panel, 'p', 'Accepted counties: ' + book.accepted_counties.join(', '));
  const rules = append(panel, 'ul');
  for (const rule of book.rules) {
    const row = append(rules, 'li');
    append(row, 'strong', rule.id + ' · ' + rule.title + ' ');
    row.append(rule.text);
  }
}

function renderEligibility() {
  const card = el('eligibility');
  card.replaceChildren();
  card.className = 'eligibility-card';
  const result = current.eligibility;
  if (!result) {
    append(card, 'p', 'No rules check is available for this fax.');
    return;
  }
  const label = append(card, 'div', undefined, 'eligibility-line');
  append(label, 'span', acceptanceLabels[result.status] || 'Check required', 'tag ' + result.status);
  append(label, 'span', result.rule_id, 'small');
  append(card, 'p', result.reason);
  if (acceptanceActions[result.action]) append(card, 'p', acceptanceActions[result.action]);
  if (result.source) append(card, 'p', 'Evidence: ' + result.source, 'small');
}

function faxPageCount() {
  return current?.fax?.page_count || documentData?.pages.length || 1;
}

function setViewer(tab, page = activeFaxPage) {
  activeTab = tab;
  activeFaxPage = Math.max(1, Math.min(page, faxPageCount()));
  el('document').scrollTop = 0;
  renderDocument();
}

function renderAttachments() {
  const attachments = current.attachments || [];
  const strip = el('attachment-strip');
  const jump = el('attachment-jump');
  strip.hidden = attachments.length === 0;
  jump.hidden = strip.hidden;
  jump.textContent = 'Images (' + attachments.length + ') ↓';
  jump.setAttribute('aria-label', 'Jump to image attachments for ' + current.id);
  el('attachment-count').textContent = String(attachments.length);
  const cards = el('attachment-cards');
  cards.replaceChildren();
  for (const attachment of attachments) {
    const card = append(cards, 'button', undefined, 'attachment-card');
    card.type = 'button';
    card.dataset.viewer = attachment.type === 'lab' ? 'fax' : attachment.type;
    if (attachment.type === 'lab') card.dataset.page = attachment.page;
    card.setAttribute('aria-label', 'Open ' + attachment.label + ' for ' + current.id);
    const preview = append(card, 'span', undefined, 'attachment-thumbnail ' + attachment.type);
    const image = append(preview, 'img');
    image.alt = '';
    image.addEventListener('error', () => {
      image.remove();
      append(preview, 'span', 'No preview', 'missing-image');
    }, {once: true});
    image.src = attachment.url;
    const copy = append(card, 'span');
    append(copy, 'strong', attachment.label);
    append(copy, 'small', attachment.detail);
    card.addEventListener('click', () => {
      setViewer(card.dataset.viewer, attachment.page);
      el('document').scrollIntoView({block: 'nearest'});
    });
  }
}

function renderAttachmentImage(panel, type, alt) {
  const attachment = (current.attachments || []).find(item => item.type === type);
  if (!attachment) {
    append(panel, 'p', 'This referral has no ' + (type === 'scan' ? 'scanned report image.' : 'MRI image.'));
    return;
  }
  const figure = append(panel, 'figure');
  const image = append(figure, 'img', undefined, type === 'mri' ? 'mri' : undefined);
  image.alt = alt;
  image.addEventListener('error', () => {
    image.remove();
    append(figure, 'p', 'Image unavailable. The original fax PDF is linked above.', 'small');
  }, {once: true});
  image.src = attachment.url;
  append(figure, 'figcaption', attachment.label + ' · ' + attachment.detail);
}

function renderFaxPage(panel) {
  const page = activeFaxPage;
  const pageCount = faxPageCount();
  el('fax-page-status').textContent = 'Page ' + page + ' of ' + pageCount;
  el('fax-prev').disabled = page === 1;
  el('fax-next').disabled = page === pageCount;
  const figure = append(panel, 'figure');
  const image = append(figure, 'img', undefined, 'fax-page');
  const suffix = page === 1 ? '-fax.png' : '-fax-' + page + '.png';
  image.src = '/referral-assets/' + current.id + suffix;
  image.alt = 'Page ' + page + ' of the simulated inbound referral fax';
  image.addEventListener('error', () => {
    if (!image.isConnected) return;
    image.remove();
    append(figure, 'p', 'Page image unavailable. Open the original PDF above or choose Extracted text.', 'small');
  }, {once: true});
  const caption = append(figure, 'figcaption', 'Page ' + page + ' · ');
  if (page === 1 && /^R10[1-5]$/.test(current.id)) {
    const source = append(caption, 'a', 'VA 10-10172 template');
    source.href = 'https://www.va.gov/forms/10-10172/';
    source.target = '_blank';
    source.rel = 'noopener';
    caption.append(' · synthetic entries');
  } else if (page === 1) {
    caption.append('Synthetic fax cover');
  } else {
    caption.append(page === 2 && pageCount === 3 ? 'Synthetic radiology report' : 'Synthetic laboratory attachment');
  }
}

function renderDocument() {
  const panel = el('document');
  panel.replaceChildren();
  for (const tab of tabs) tab.setAttribute('aria-pressed', String(tab.dataset.tab === activeTab));
  for (const card of el('attachment-cards').querySelectorAll('button')) {
    const selected = card.dataset.viewer === activeTab &&
      (activeTab !== 'fax' || Number(card.dataset.page) === activeFaxPage);
    card.setAttribute('aria-pressed', String(selected));
  }
  el('fax-pager').hidden = activeTab !== 'fax';
  if (activeTab === 'fax') {
    renderFaxPage(panel);
    return;
  }
  if (activeTab === 'scan') {
    renderAttachmentImage(panel, 'scan', 'Fictional signed radiology addendum with a critical routing instruction');
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
    renderAttachmentImage(panel, 'mri', 'Axial slice from the BrainWeb simulated normal brain MRI');
    return;
  }
  if (!documentData) {
    append(panel, 'p', documentError || 'Extracting PDF text…', 'small');
    return;
  }
  documentData.pages.forEach((page, index) => {
    const details = append(panel, 'details');
    details.open = index === 0;
    append(details, 'summary', 'PDF page ' + (index + 1));
    append(details, 'pre', page);
    if (index === 1 && current.attachments?.some(attachment => attachment.type === 'scan')) {
      const button = append(details, 'button', 'Open scanned addendum');
      button.type = 'button';
      button.addEventListener('click', () => setViewer('scan'));
    }
  });
}

function renderSnapTrace(card, summary) {
  if (!summary) return;
  const details = append(card, 'details');
  append(details, 'summary', 'Inspect Snap request');
  append(details, 'p', summary.model + ' · ' + summary.endpoint, 'metric');
  const inputs = {
    intake_fields: summary.intake_fields,
    pdf_pages: summary.page_labels,
    images: summary.image_labels,
    clinic_rules: summary.policy_version,
    evidence_handling: summary.evidence_instructions,
    questions: summary.questions || summary.question_names,
  };
  append(details, 'pre', JSON.stringify(inputs, null, 2));
}

function renderMuse() {
  const button = el('muse-check');
  const panel = el('muse-result');
  panel.replaceChildren();
  button.disabled = !inbox.muse_available || musePending !== null;
  button.textContent = musePending === current.id ? 'Checking the PDF…' : 'Verify PDF with Muse';
  if (!inbox.muse_available) {
    append(panel, 'p', 'Muse credentials are unavailable for this workspace.');
    return;
  }
  const result = museResults.get(current.id);
  if (!result) return;
  if (!result.available) {
    append(panel, 'p', result.message || 'Muse did not return a PDF cross-check.');
    return;
  }
  const status = result.verified
    ? (result.quote ? 'Source quote verified' : 'Missing signed report verified')
    : 'PDF check unverified';
  append(panel, 'span', status, 'tag ' + (result.verified ? 'success' : 'neutral'));
  if (result.priority) append(panel, 'p', 'Muse read: ' + (routes[result.priority] || result.priority));
  if (result.quote) append(panel, 'blockquote', result.quote);
  const metrics = [result.model];
  if (Number.isFinite(result.elapsed_ms)) metrics.push((result.elapsed_ms / 1000).toFixed(2) + ' s');
  if (Number.isFinite(result.input_tokens)) metrics.push(result.input_tokens.toLocaleString() + ' input tokens');
  if (Number.isFinite(result.output_tokens)) metrics.push(result.output_tokens.toLocaleString() + ' output tokens');
  append(panel, 'p', metrics.filter(Boolean).join(' · '), 'small');
  if (result.verified && Number.isInteger(result.page) && result.page >= 1 && result.page <= faxPageCount()) {
    const source = append(panel, 'button', 'Open verified page ' + result.page, 'button secondary');
    source.type = 'button';
    source.addEventListener('click', () => {
      setViewer('fax', result.page);
      el('document').scrollIntoView({block: 'nearest'});
    });
  }
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
      renderSnapTrace(card, result.request_summary);
      continue;
    }
    append(card, 'strong', routes[result.route], 'result-route ' + result.route);
    const evidence = result.source === 'SCAN' ? 'Scanned addendum, PDF page 2'
      : result.source === 'NONE' ? 'No signed instruction found'
      : 'PDF page ' + result.source.slice(1);
    append(card, 'div', evidence, 'metric');
    append(card, 'div', labLabels[result.lab], 'metric');
    if (result.acceptance) {
      append(card, 'div', 'Snap’s acceptance suggestion: ' + (acceptanceLabels[result.acceptance.status] || result.acceptance.status), 'metric');
    }
    append(card, 'div', result.pages + ' page(s) · ' + result.images + ' image(s) · ' + result.input_tokens.toLocaleString() + ' input tokens', 'small');
    const probabilities = append(card, 'details');
    append(probabilities, 'summary', 'Model scores');
    const list = append(probabilities, 'dl');
    for (const [route, probability] of Object.entries(result.probabilities)) {
      append(list, 'dt', routes[route]);
      append(list, 'dd', (probability * 100).toFixed(1) + '%');
    }
    renderSnapTrace(card, result.request_summary);
  }
}

function suggestedRoute(result) {
  const eligibility = current.eligibility?.status;
  if (result.route === 'critical' || !eligibility || eligibility === 'eligible') return result.route;
  return 'intake_coordination';
}

function renderReview() {
  const outcome = el('outcome');
  const review = current.review;
  el('review').hidden = Boolean(review);
  outcome.hidden = !review;
  outcome.replaceChildren();
  const eligibility = current.eligibility?.status;
  const bookingBlocked = eligibility && eligibility !== 'eligible';
  const guidance = el('route-guidance');
  guidance.hidden = !bookingBlocked;
  guidance.textContent = bookingBlocked
    ? 'Appointment routes are held for this specialist. Intake coordination and clinician escalation remain available.'
    : '';
  const menu = el('route');
  for (const option of menu.options) {
    option.disabled = (bookingBlocked && ['high', 'medium', 'low'].includes(option.value))
      || (eligibility === 'eligible' && option.value === 'intake_coordination');
  }
  const result = decisions.get(current.id)?.packet;
  if (!review && !menu.value && result?.available) menu.value = suggestedRoute(result);
  if (review) {
    append(outcome, 'strong', routes[review.route] + ' · ' + review.status, review.route);
    if (review.slot) append(outcome, 'div', review.slot);
    if (review.reason) append(outcome, 'div', review.reason, 'small');
  }
}

async function selectCase(item) {
  current = item;
  documentData = null;
  documentError = '';
  activeTab = 'fax';
  activeFaxPage = 1;
  el('case-id').textContent = item.id + ' · INBOUND FAX';
  el('case-title').textContent = item.fax?.sender_name || item.clinic;
  el('open-pdf').href = item.fax?.document_url || '/referral-assets/' + item.id + '.pdf';
  el('open-pdf').hidden = false;
  for (const tab of tabs.filter(tab => ['scan', 'mri'].includes(tab.dataset.tab))) {
    tab.hidden = !item.attachments?.some(attachment => attachment.type === tab.dataset.tab);
  }
  el('review').reset();
  el('error').textContent = '';
  renderQueue();
  renderFaxMetadata();
  renderAttachments();
  renderEligibility();
  el('document').scrollTop = 0;
  renderDocument();
  renderComparisons();
  renderMuse();
  renderReview();
  try {
    const result = await api('/document', {id: item.id});
    if (current === item) {
      documentData = result;
      if (['scan', 'pages'].includes(activeTab)) renderDocument();
    }
  } catch (error) {
    if (current === item) {
      documentError = error.message;
      if (activeTab === 'pages') renderDocument();
    }
  }
}

function setBusy(value) {
  pending = value;
  for (const button of controls) button.disabled = value;
  el('reset').disabled = value;
  el('review').querySelector('button').disabled = value;
}

for (const tab of tabs) {
  tab.addEventListener('click', () => setViewer(tab.dataset.tab));
}

function moveFaxPage(offset) {
  if (!current) return;
  activeFaxPage = Math.max(1, Math.min(faxPageCount(), activeFaxPage + offset));
  el('document').scrollTop = 0;
  renderDocument();
}

el('fax-prev').addEventListener('click', () => moveFaxPage(-1));
el('fax-next').addEventListener('click', () => moveFaxPage(1));
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
          el('route').value = suggestedRoute(result);
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

el('muse-check').addEventListener('click', async () => {
  if (!inbox?.muse_available || !current || musePending !== null) return;
  const id = current.id;
  musePending = id;
  museResults.delete(id);
  renderMuse();
  try {
    museResults.set(id, await api('/muse', {id}));
  } catch (error) {
    museResults.set(id, {available: false, message: error.message});
  } finally {
    musePending = null;
    renderMuse();
  }
});

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
  if (pending || musePending !== null) return;
  setBusy(true);
  try {
    inbox = await api('/reset', {});
    decisions.clear();
    museResults.clear();
    el('queue-search').value = '';
    renderRulesBook();
    await selectCase(inbox.cases[0]);
    void triageMap?.reset();
  } catch (error) {
    el('error').textContent = error.message;
  } finally {
    setBusy(false);
  }
});

el('queue-search').addEventListener('input', () => { if (inbox) renderQueue(); });

async function start() {
  inbox = await api();
  el('live').textContent = inbox.model_available ? 'LIVE AVAILABLE' : 'MANUAL REVIEW';
  el('policy').textContent = inbox.policy;
  renderRulesBook();
  triageMap = createTriageMap({
    api, cases: () => inbox.cases, modelAvailable: inbox.model_available,
    onUpdate: renderQueue,
    openFax: (item, source) => {
      const page = source?.endsWith('-SCAN') ? 2 : Number(source?.match(/-P(\d+)$/)?.[1] || 1);
      void selectCase(item);
      const scan = source?.endsWith('-SCAN') && item.attachments?.some(attachment => attachment.type === 'scan');
      setViewer(scan ? 'scan' : 'fax', page);
      el('fax-workspace').scrollIntoView({block: 'start'});
    },
  });
  const selected = selectCase(inbox.cases[0]);
  if (inbox.model_available) void triageMap.classify();
  await selected;
}
start().catch(error => { el('error').textContent = error.message; });

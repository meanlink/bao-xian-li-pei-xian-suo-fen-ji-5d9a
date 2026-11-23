import {get, post} from '../api.js';
import {Layout, Section, openModal, showToast} from '../components/ui.js';
import {
  claimStatus, clueStatus, clueReviewStatus, clueEvidenceSource, clueReviewClaimStatuses,
  escapeHtml, riskLevel, withdrawalOutcome,
} from '../utils/constants.js';

const reviewTone = {unsubmitted:'badge-gray', pending:'badge-amber', returned:'badge-red', resolved:'badge-mint'};
const outcomeTone = {invalidated:'badge-red', returned:'badge-amber', retained:'badge-mint'};
const roleHint = {
  intake_agent:'按案件查看线索核验进度与主管结论。',
  investigator:'补充可追溯的凭证与核验发现，交由风险主管复核。',
  risk_manager:'核对补充证据，确认核验建议或退回补正；发证方撤回凭证时按来源与编号登记。',
};

function stateOf(item) {
  return item.review_status || 'unsubmitted';
}

function reviewBadge(item) {
  const state = stateOf(item);
  return `<span class="badge ${reviewTone[state]}">${clueReviewStatus[state]}</span>`;
}

function detailMarkup(item, user) {
  if (!item) return '<div class="empty">当前筛选没有线索。可切换状态或回到全部案件。</div>';
  const state = stateOf(item);
  const withdrawn = Boolean(item.credential_withdrawn);
  const eligible = item.clue_status === 'open' && clueReviewClaimStatuses.includes(item.claim_status);
  const canSubmit = eligible && user.role === 'investigator' && (
    state === 'unsubmitted' || (state === 'returned' && item.submitted_by === user.id)
  );
  // Keep the action visible when withdrawn so the API can explain the block,
  // and the manager sees why this credential cannot be adopted.
  const canDecide = eligible && user.role === 'risk_manager' && state === 'pending';
  const action = canSubmit ? `<button class="btn btn-primary" data-review-action="submission">${state === 'returned' ? '补正并重新提交' : '提交核验证据'}</button>` : '';
  const decision = canDecide ? '<button class="btn btn-primary" data-review-action="decision">填写复核决定</button>' : '';
  const claimLink = `<button class="btn btn-line" data-nav="detail" data-detail="${item.claim_id}">打开案件</button>`;
  const timestamp = value => new Date(value).toLocaleString('zh-CN', {hour12:false});
  const submission = item.revision ? `
    <div class="review-record">
      <div class="split"><h3>补充核验</h3><span class="muted">第 ${item.revision} 轮</span></div>
      <dl class="review-fields">
        <div><dt>凭证来源</dt><dd>${clueEvidenceSource[item.evidence_source]}</dd></div>
        <div><dt>凭证编号</dt><dd>${escapeHtml(item.evidence_reference)}</dd></div>
        <div><dt>调查员建议</dt><dd>${clueStatus[item.recommendation]}</dd></div>
        <div><dt>提交记录</dt><dd>${escapeHtml(item.submitter)} · ${timestamp(item.submitted_at)}</dd></div>
      </dl>
      <p class="review-text">${escapeHtml(item.finding)}</p>
    </div>` : '<div class="empty">暂无补充核验。案件进入调查后可提交凭证。</div>';
  const withdrawnNotice = withdrawn ? `
    <div class="notice danger">
      <strong>凭证已被发证方撤回</strong>
      <p class="review-text">${clueEvidenceSource[item.evidence_source]} · ${escapeHtml(item.evidence_reference)} 已不能再送审或采纳，也不能据此办结案件。</p>
    </div>` : '';
  const review = item.review_note ? `
    <div class="notice ${state === 'returned' ? 'danger' : ''}">
      <strong>${state === 'returned' ? '退回原因' : '主管结论'} · ${escapeHtml(item.reviewer)}</strong>
      <p class="review-text">${escapeHtml(item.review_note)}</p>
    </div>` : '';
  return `<div class="review-heading"><div><div class="subline">${escapeHtml(item.claim_no)} · ${claimStatus[item.claim_status]}</div>
    <h2>${escapeHtml(item.clue_type)}</h2></div>${reviewBadge(item)}</div>
    <div class="review-record"><h3>原始线索 <span class="subline">${riskLevel[item.severity]} · ${clueStatus[item.clue_status]}</span></h3>
    <p class="review-text">${escapeHtml(item.description)}</p><p class="review-text muted">${escapeHtml(item.evidence)}</p></div>
    ${withdrawnNotice}${submission}${review}<div class="review-actions">${action}${decision}${claimLink}</div>`;
}

function openReviewEditor(kind, item, onSaved) {
  const submitting = kind === 'submission';
  const title = submitting ? '提交核验证据' : '填写复核决定';
  const sources = Object.entries(clueEvidenceSource).map(([key, label]) =>
    `<option value="${key}" ${item.evidence_source === key ? 'selected' : ''}>${label}</option>`).join('');
  const modal = openModal(title, `<form id="clue-review-form" class="form-grid">
    ${submitting ? `
    <label class="field">凭证来源<select name="evidence_source" required><option value="">请选择</option>${sources}</select></label>
    <label class="field">凭证编号<input name="evidence_reference" placeholder="例如 WX-261105-07" minlength="3" maxlength="120" value="${escapeHtml(item.evidence_reference)}" required></label>
    <label class="field full">核验发现<textarea name="finding" placeholder="说明核对了什么，以及凭证如何支持判断" minlength="10" maxlength="1000" required>${escapeHtml(item.finding)}</textarea></label>
    <label class="field full">调查员建议<select name="recommendation" required><option value="">请选择</option>
      <option value="confirmed" ${item.recommendation === 'confirmed' ? 'selected' : ''}>证实线索</option>
      <option value="dismissed" ${item.recommendation === 'dismissed' ? 'selected' : ''}>排除线索</option></select></label>` : `
    <label class="field full">复核决定<select name="action" required><option value="">请选择</option>
      <option value="accept">采纳建议 · ${clueStatus[item.recommendation]}</option><option value="return">退回补正</option></select></label>
    <label class="field full">复核说明<textarea name="note" placeholder="记录采纳依据或还需补充的凭证" minlength="6" maxlength="500" required></textarea></label>`}
    <p class="review-error full" role="alert" hidden></p>
    <div class="full"><button class="btn btn-primary" type="submit">${submitting ? '提交主管复核' : '保存决定'}</button></div></form>`);
  const form = modal.querySelector('form');
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const fields = Object.fromEntries(new FormData(form));
    const button = form.querySelector('[type=submit]');
    const error = form.querySelector('[role=alert]');
    button.disabled = true;
    error.hidden = true;
    try {
      await post(`/risk-clues/${item.clue_id}/review/${kind}`, {...fields, expected_revision:item.revision || 0});
      modal.remove();
      showToast(submitting ? '证据已提交' : '复核决定已保存');
      await onSaved();
    } catch (err) {
      if (modal.isConnected) {
        error.textContent = err.message;
        error.hidden = false;
      } else {
        showToast(`已保存，刷新队列失败。${err.message}`);
      }
    } finally {
      button.disabled = false;
    }
  });
}

function withdrawalMarkup(withdrawals) {
  if (!withdrawals.length) {
    return '<p class="empty">暂无凭证撤回登记。发证方撤回凭证后在此按来源与编号登记，相关案件会一并记录处理结果。</p>';
  }
  const timestamp = value => new Date(value).toLocaleString('zh-CN', {hour12:false});
  return withdrawals.map(entry => `
    <div class="review-record">
      <div class="split">
        <h3>${clueEvidenceSource[entry.evidence_source]} · ${escapeHtml(entry.evidence_reference)}</h3>
        <span class="muted">${escapeHtml(entry.withdrawn_by_name)} · ${timestamp(entry.withdrawn_at)}</span>
      </div>
      <p class="review-text">${escapeHtml(entry.withdrawal_note)}</p>
      ${entry.impacts.length ? `<ul class="withdrawal-impacts">${entry.impacts.map(impact => `
        <li><button class="btn btn-line" data-nav="detail" data-detail="${impact.claim_id}">${escapeHtml(impact.claim_no)}</button>
          <span>${escapeHtml(impact.clue_type)}</span>
          <span class="badge ${outcomeTone[impact.outcome]}">${withdrawalOutcome[impact.outcome]}</span></li>`).join('')}</ul>`
        : '<p class="subline">登记时没有案件引用该凭证。</p>'}
    </div>`).join('');
}

function openWithdrawalEditor(onSaved) {
  const sources = Object.entries(clueEvidenceSource).map(([key, label]) => `<option value="${key}">${label}</option>`).join('');
  const modal = openModal('登记凭证撤回', `<form id="withdrawal-form" class="form-grid">
    <label class="field">凭证来源<select name="evidence_source" required>${sources}</select></label>
    <label class="field">凭证编号<input name="evidence_reference" placeholder="例如 WX-261105-07" minlength="3" maxlength="120" required></label>
    <label class="field full">撤回说明<textarea name="withdrawal_note" placeholder="记录发证方撤回的时间、原因与经办人" minlength="6" maxlength="500" required></textarea></label>
    <p class="review-error full" role="alert" hidden></p>
    <div class="full"><button class="btn btn-primary" type="submit">登记撤回</button></div></form>`);
  const form = modal.querySelector('form');
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const button = form.querySelector('[type=submit]');
    const error = form.querySelector('[role=alert]');
    button.disabled = true;
    error.hidden = true;
    try {
      const result = await post('/evidence-withdrawals', Object.fromEntries(new FormData(form)));
      modal.remove();
      const count = result.impacts.length;
      const duplicate = result.duplicate ? '，此前已登记，本次未重复记录' : '';
      showToast(`凭证撤回已登记，涉及 ${count} 个案件${duplicate}`);
      await onSaved();
    } catch (err) {
      if (modal.isConnected) {
        error.textContent = err.message;
        error.hidden = false;
      } else {
        showToast(`已登记，刷新工作台失败。${err.message}`);
      }
    } finally {
      button.disabled = false;
    }
  });
}

export async function renderClueReviews(user, navigate, claimId) {
  const endpoint = `/clue-reviews${claimId ? `?claim_id=${claimId}` : ''}`;
  const [{items}, {items: withdrawals}] = await Promise.all([get(endpoint), get('/evidence-withdrawals')]);
  const options = Object.entries(clueReviewStatus).map(([key, value]) => `<option value="${key}">${value}</option>`).join('');
  const content = `<div class="page-head"><div><h1 class="page-title">线索证据复核</h1><p class="page-subtitle">${roleHint[user.role]}</p></div>
    <div class="actions">${user.role === 'risk_manager' ? '<button class="btn btn-primary" id="register-withdrawal">登记凭证撤回</button>' : ''}
    ${claimId ? '<button class="btn btn-line" data-nav="clueReviews">查看全部案件</button>' : ''}</div></div>
    <div class="review-workspace" id="review-workspace">
      ${Section(claimId ? '本案线索' : '线索队列', '<div id="review-queue"></div>', `<div class="review-filter"><label for="review-filter">状态</label><select id="review-filter"><option value="all">全部</option>${options}</select></div>`)}
      <section class="card section" id="review-detail" aria-label="证据详情"></section>
    </div>
    ${Section('凭证撤回登记', `<div id="withdrawal-log">${withdrawalMarkup(withdrawals)}</div>`,
      '<span class="subline">按来源与编号登记，涉及案件的处理结果同步留痕</span>')}`;
  setTimeout(() => {
    const workspace = document.querySelector('#review-workspace');
    if (!workspace) return;
    const queue = workspace.querySelector('#review-queue');
    const detail = workspace.querySelector('#review-detail');
    const filter = workspace.querySelector('#review-filter');
    let rows = items;
    let selectedId = rows[0]?.clue_id;
    function paint() {
      const visible = rows.filter(item => filter.value === 'all' || stateOf(item) === filter.value);
      const selected = visible.find(item => item.clue_id === selectedId) || visible[0];
      selectedId = selected?.clue_id;
      queue.innerHTML = visible.map(item => `<button class="review-queue-item" data-clue-id="${item.clue_id}" aria-pressed="${item.clue_id === selectedId}">
        <span><strong>${escapeHtml(item.clue_type)}</strong><small>${escapeHtml(item.claim_no)}</small></span>
        ${item.credential_withdrawn ? '<span class="badge badge-gray">凭证已撤回</span> ' : ''}${reviewBadge(item)}</button>`).join('')
        || '<p class="empty">暂无符合条件的线索</p>';
      detail.innerHTML = detailMarkup(selected, user);
      detail.querySelector('[data-review-action]')?.addEventListener('click', event => {
        openReviewEditor(event.currentTarget.dataset.reviewAction, selected, async () => {
          rows = (await get(endpoint)).items;
          filter.value = 'all';
          paint();
        });
      });
    }
    document.querySelector('#register-withdrawal')?.addEventListener('click', () => {
      openWithdrawalEditor(async () => {
        const [reviewData, withdrawalData] = await Promise.all([get(endpoint), get('/evidence-withdrawals')]);
        rows = reviewData.items;
        document.querySelector('#withdrawal-log').innerHTML = withdrawalMarkup(withdrawalData.items);
        filter.value = 'all';
        paint();
      });
    });
    queue.addEventListener('click', event => {
      const button = event.target.closest('[data-clue-id]');
      if (button) {
        selectedId = Number(button.dataset.clueId);
        paint();
        queue.querySelector('[aria-pressed="true"]')?.focus();
      }
    });
    filter.addEventListener('change', paint);
    paint();
  }, 0);
  return Layout({user, active:'clueReviews', onNavigate:navigate, content});
}

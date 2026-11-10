import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const { chromium, webkit } = createRequire(import.meta.url)('playwright');
const engine = process.env.BROWSER_ENGINE === 'webkit' ? webkit : chromium;
const target = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4197');
if (!['127.0.0.1', 'localhost'].includes(target.hostname) || !['http:', 'https:'].includes(target.protocol)
  || target.pathname !== '/' || target.username || target.password || target.search || target.hash) {
  throw new Error('Admission acceptance must use an isolated local origin');
}
const base = target.origin;
const prefix = '/v1/membership/admission';
const now = '2030-01-02T02:00:00Z';
const profile = {
  legal_name: '隔離申請人', phone: '0912345678', birth_date: '2000-01-01', address: '隔離市合作路1號',
  emergency_contact: '隔離聯絡人 0987654321', contact_email: 'applicant@example.test', gender: null,
  place_of_origin: null, occupation: null, registered_address: null, correspondence_address: null, landline_phone: null, line_id: null,
};
const config = {
  flow_version: 'education-review-v1',
  material: { version: 'isolated-material-v1', title: '隔離入社教材', url: `${base}/isolated-material`, sections: [{ title: '隔離閱讀重點', body: '這是隔離驗收資料。' }] },
  quiz: { version: 'isolated-quiz-v1', pass_score: 80, points_per_question: 10, questions: Array.from({ length: 10 }, (_, index) => ({ id: `q${index + 1}`, question: `隔離題目 ${index + 1}`, options: [{ value: 'A', label: '隔離選項甲' }, { value: 'B', label: '隔離選項乙' }] })) },
  declaration: {
    version: 'isolated-declaration-v1', privacy_version: 'isolated-privacy-v1', privacy_url: `${base}/isolated-privacy`,
    rights: ['隔離社員權利'], obligations: ['隔離社員義務'], statement: '隔離入社志願書，敬請許可入社。',
    share_unit_amount: 100, student_min_shares: 5, student_max_shares: 10, general_min_shares: 10, general_max_shares: 100,
  }, discovery_channels: ['朋友介紹', '其他'],
};
const newRound = () => ({
  id: 'isolated-round', user_id: 'applicant', round_number: 1, version: 1, flow_version: 'education-review-v1', status: 'draft',
  profile: null, requested_share_count: 10, student_claimed: false, discovery_channel: null, discovery_other: null,
  declaration_version: null, privacy_version: null, consented_at: null, material_version: null, material_completed_at: null,
  quiz_version: config.quiz.version, quiz_eligibility: { allowed: true, remaining_attempts: 1, next_available_at: null, reason: null, settings_configured: false },
  attempts: [], submissions: [], reviews: [], submitted_at: null, reviewed_at: null, review_reason: null,
  student_verified: null, decision_reference: null, created_at: now, updated_at: now, can_edit: true, can_submit: false, can_withdraw: true,
});

async function fixture(t, width, { conflict = false, score = 80, member = false, hasLegacy = false, admissionFee = 500, shareUnit = 100 } = {}) {
  const viewConfig = structuredClone(config);
  viewConfig.declaration.admission_fee_amount = admissionFee;
  viewConfig.declaration.share_unit_amount = shareUnit;
  const browser = await engine.launch({ headless: true, ...(engine === chromium ? { executablePath: process.env.BROWSER_EXECUTABLE } : {}) });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width, height: 1000 }, timezoneId: 'Asia/Taipei', locale: 'zh-TW', serviceWorkers: 'block' });
  const state = { round: null, history: [], writes: [], unknown: [], errors: [], reads: 0, conflict, denyReads: false, denyMutation: false };
  await context.route('**/*', route => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (url.origin !== base) return route.abort();
    if (!path.startsWith('/v1/')) return request.method() === 'GET' ? route.continue() : route.abort();
    if (path === '/v1/auth/refresh') return route.fulfill({ json: { access_token: 'isolated-token', user: { id: 'applicant', email: profile.contact_email, display_name: '隔離帳號', user_role: 'customer', membership_type: member ? 'member' : 'nonmember' } } });
    if (path === '/v1/members/me') return route.fulfill({ json: { membership_type: member ? 'member' : 'nonmember', membership: member ? { status: 'active', member_number: 'ISOLATED001' } : null } });
    if (path === '/v1/platform') return route.fulfill({ json: { orders_open: true, sales_scope: 'pickup' } });
    if (path === prefix && request.method() === 'GET') {
      state.reads++;
      if (state.denyReads) return route.fulfill({ status: 401, json: { detail: '隔離登入已失效' } });
      const history = [...state.history, ...(state.round ? [state.round] : [])].map(value => ({ id: value.id, round_number: value.round_number, status: value.status, created_at: value.created_at, reviewed_at: value.reviewed_at }));
      return route.fulfill({ json: { config: viewConfig, current_round: state.round, history, profile_defaults: null, has_legacy_application: hasLegacy, can_start: !state.round || ['withdrawn', 'rejected'].includes(state.round.status), start_blocked_reason: null } });
    }
    if (request.method() === 'GET' && path.startsWith(`${prefix}/rounds/`)) {
      const value = [...state.history, state.round].find(item => item?.id === path.split('/').at(-1));
      return value ? route.fulfill({ json: value }) : route.fulfill({ status: 404, json: { detail: '找不到隔離歷史輪次' } });
    }
    if (!path.startsWith(`${prefix}/rounds`) || request.method() === 'GET') {
      state.unknown.push(`${request.method()} ${path}`);
      return route.fulfill({ status: 404, json: { detail: 'Undefined isolated endpoint' } });
    }
    const body = request.postDataJSON();
    state.writes.push({ method: request.method(), path, body });
    if (state.denyMutation) return route.fulfill({ status: 403, json: { detail: '隔離權限已失效' } });
    if (path === `${prefix}/rounds`) {
      assert.match(body.idempotency_key, /^[0-9a-f-]{36}$/);
      if (state.round && ['withdrawn', 'rejected'].includes(state.round.status)) {
        state.history.push(structuredClone(state.round));
        state.round = { ...newRound(), id: `isolated-round-${state.history.length + 1}`, round_number: state.history.length + 1 };
      } else state.round ||= newRound();
      return route.fulfill({ json: state.round });
    }
    assert.equal(body.expected_version, state.round.version);
    if (request.method() === 'PUT') {
      if (state.conflict) {
        state.conflict = false;
        state.round = { ...state.round, version: state.round.version + 1, profile: { ...profile, legal_name: '另一頁已儲存姓名' } };
        return route.fulfill({ status: 409, json: { detail: '隔離版本衝突' } });
      }
      assert.deepEqual(Object.keys(body.profile).sort(), Object.keys(profile).sort());
      assert.equal(body.declaration_accepted, true);
      assert.equal(body.privacy_accepted, true);
      assert.equal(body.share_rules_understood, true);
      assert.equal(body.payment_notice_understood, true);
      state.round = { ...state.round, ...body, version: state.round.version + 1, consented_at: now };
    } else if (path.endsWith('/education')) {
      assert.equal(body.read_confirmed, true);
      assert.equal(body.material_version, config.material.version);
      state.round = { ...state.round, version: state.round.version + 1, material_version: body.material_version, material_completed_at: now };
    } else if (path.endsWith('/quiz-attempts')) {
      assert.equal(body.answers.length, 10);
      assert.equal(new Set(body.answers.map(answer => answer.question_id)).size, 10);
      assert.equal('score' in body, false);
      const attempt = { id: 'attempt-1', attempt_number: 1, quiz_version: config.quiz.version, score, passed: score >= 80, submitted_at: now, settings_version: 0,
        results: body.answers.map((answer, index) => ({ question_id: answer.question_id, selected_option: answer.option, correct_option: index < score / 10 ? 'A' : 'B', correct: index < score / 10 })) };
      state.round = { ...state.round, version: state.round.version + 1, attempts: [attempt], can_submit: score >= 80,
        quiz_eligibility: { allowed: false, remaining_attempts: 0, next_available_at: null, reason: score < 80 ? '重考次數尚未設定，目前只允許首次交卷' : '本輪已合格', settings_configured: false } };
    } else if (path.endsWith('/submit')) {
      assert.equal(state.round.attempts[0].passed, true);
      state.round = { ...state.round, version: state.round.version + 1, status: 'submitted', can_submit: false, can_edit: false, submitted_at: now, submissions: [{ id: 'submission-1', revision_number: 1, submitted_at: now }] };
    } else if (path.endsWith('/withdraw')) {
      state.round = { ...state.round, version: state.round.version + 1, status: 'withdrawn', can_submit: false, can_edit: false, can_withdraw: false };
    } else {
      state.unknown.push(`${request.method()} ${path}`);
      return route.fulfill({ status: 404, json: { detail: 'Undefined isolated mutation' } });
    }
    return route.fulfill({ json: state.round });
  });
  const page = await context.newPage();
  page.setDefaultTimeout(7000);
  page.on('pageerror', error => state.errors.push(error.message));
  await page.goto(`${base}/membership`);
  return { page, state };
}

async function fillProfile(page) {
  await page.getByRole('button', { name: '開始入社申請', exact: true }).click();
  assert.equal(await page.getByLabel(/身分證|身份證|證件/).count(), 0);
  assert.equal(await page.locator('input[type=file]').count(), 0);
  for (const checkbox of await page.getByRole('checkbox').all()) assert.equal(await checkbox.isChecked(), false);
  await page.getByLabel('姓名', { exact: true }).fill(profile.legal_name);
  await page.getByLabel('出生日期', { exact: true }).fill(profile.birth_date);
  await page.getByLabel('手機', { exact: true }).fill(profile.phone);
  await page.getByLabel('聯絡 Email', { exact: true }).fill(profile.contact_email);
  await page.getByLabel('現居地址', { exact: true }).fill(profile.address);
  await page.getByLabel('緊急聯絡人與電話', { exact: true }).fill(profile.emergency_contact);
  await page.getByLabel('如何得知合作社', { exact: true }).selectOption('朋友介紹');
  assert.equal(await page.getByRole('button', { name: '儲存並閱讀教材', exact: true }).isDisabled(), true);
  for (const checkbox of await page.getByRole('checkbox').all()) await checkbox.check();
}

async function completeEducation(page) {
  await page.getByRole('button', { name: '儲存並閱讀教材', exact: true }).click();
  await page.getByRole('heading', { name: '閱讀入社教材', exact: true }).waitFor();
  const read = page.getByLabel('我已閱讀本次入社教材及以上閱讀重點。', { exact: true });
  assert.equal(await read.isChecked(), false);
  assert.equal(await page.getByRole('button', { name: '確認閱讀並開始測驗', exact: true }).isDisabled(), true);
  await read.check();
  await page.getByRole('button', { name: '確認閱讀並開始測驗', exact: true }).click();
  await page.getByRole('heading', { name: '入社測驗', exact: true }).waitFor();
}

async function completeQuiz(page) {
  assert.equal(await page.getByText(/正確答案：/).count(), 0);
  assert.equal(await page.getByRole('button', { name: '交卷並查看結果', exact: true }).isDisabled(), true);
  for (const question of await page.locator('.admission-quiz-question').all()) await question.getByRole('radio').first().check();
  await page.getByRole('button', { name: '交卷並查看結果', exact: true }).click();
  await page.getByRole('region', { name: '本次測驗結果', exact: true }).waitFor();
  assert.equal(await page.getByText(/正確答案：/).count(), 10);
}

function verifyState(state) {
  assert.deepEqual(state.unknown, []);
  assert.deepEqual(state.errors, []);
  assert.ok(state.writes.every(write => write.path.startsWith(`${prefix}/rounds`)));
}

for (const width of [1440, 390]) {
  test(`${width}px 資料明確同意→閱讀→10題由server評80分→送審；核准後不開付款或授資格`, async t => {
    const { page, state } = await fixture(t, width);
    await fillProfile(page);
    await completeEducation(page);
    await completeQuiz(page);
    assert.equal(await page.getByText('80 分', { exact: true }).count(), 1);
    await page.getByRole('button', { name: '前往送出申請', exact: true }).click();
    await page.getByRole('button', { name: '送出入社申請', exact: true }).click();
    await page.locator('.admission-progress-list dd').filter({ hasText: /^審核中$/ }).waitFor();
    state.round = { ...state.round, version: state.round.version + 1, status: 'approved', reviewed_at: now, review_reason: '隔離審核同意', decision_reference: 'ISOLATED-DECISION', can_withdraw: false };
    await page.getByRole('button', { name: '更新申請進度', exact: true }).click();
    await page.getByText(/目前尚未取得正式社員資格/).waitFor();
    assert.equal(await page.getByRole('button', { name: /付款|繳費/ }).count(), 0);
    assert.equal(await page.getByRole('link', { name: /付款|繳費/ }).count(), 0);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    verifyState(state);
    if (process.env.BROWSER_SCREENSHOT_DIR) await page.screenshot({ path: `${process.env.BROWSER_SCREENSHOT_DIR}/入社第一階段-${width}.png`, fullPage: true });
  });
}

test('409不覆蓋填寫；明確重讀保草稿並用新expected_version才可儲存', async t => {
  const { page, state } = await fixture(t, 390, { conflict: true });
  await fillProfile(page);
  await page.getByRole('button', { name: '儲存並閱讀教材', exact: true }).click();
  await page.getByRole('alert').getByText(/申請資料已變更/).waitFor();
  assert.equal(await page.getByLabel('姓名', { exact: true }).inputValue(), profile.legal_name);
  assert.equal(await page.getByRole('button', { name: '儲存並閱讀教材', exact: true }).isDisabled(), true);
  await page.getByRole('button', { name: '重新讀取最新進度', exact: true }).click();
  await page.getByText('已讀取最新進度，填寫內容仍保留。請確認後再儲存。', { exact: true }).waitFor();
  assert.equal(await page.getByLabel('姓名', { exact: true }).inputValue(), profile.legal_name);
  await page.getByRole('button', { name: '儲存並閱讀教材', exact: true }).click();
  await page.getByRole('heading', { name: '閱讀入社教材', exact: true }).waitFor();
  assert.deepEqual(state.writes.filter(write => write.method === 'PUT').map(write => write.body.expected_version), [1, 2]);
  verifyState(state);
});

test('未設定重考只允許首次；70分顯示對錯但不可送審或自行重新交卷', async t => {
  const { page, state } = await fixture(t, 390, { score: 70 });
  await fillProfile(page);
  await completeEducation(page);
  await completeQuiz(page);
  await page.getByText('70 分', { exact: true }).waitFor();
  assert.equal(await page.getByRole('button', { name: '開始重考', exact: true }).count(), 0);
  assert.equal(await page.getByRole('button', { name: '前往送出申請', exact: true }).count(), 0);
  await page.getByText('重考次數尚未設定，目前只允許首次交卷', { exact: true }).waitFor();
  await page.getByRole('button', { name: '更新測驗資格', exact: true }).click();
  assert.equal(state.writes.filter(write => write.path.endsWith('/quiz-attempts')).length, 1);
  verifyState(state);
});

test('正式社員保留資格頁，不建立新版申請', async t => {
  const { page, state } = await fixture(t, 390, { member: true });
  await page.getByRole('heading', { name: '你已是正式社員', exact: true }).waitFor();
  assert.equal(state.reads, 0);
  assert.deepEqual(state.writes, []);
  verifyState(state);
});

test('已載入後GET 401或寫入403均立即遮蔽入社個資與答案', async t => {
  await t.test('GET 401', async child => {
    const { page, state } = await fixture(child, 390);
    await fillProfile(page);
    await completeEducation(page);
    await completeQuiz(page);
    await page.getByRole('button', { name: '前往送出申請', exact: true }).click();
    state.denyReads = true;
    await page.getByRole('button', { name: '更新申請進度', exact: true }).click();
    await page.getByRole('button', { name: '登入後開始', exact: true }).waitFor();
    assert.equal(await page.locator('.admission-panel, .admission-history, input[name^="admission_quiz_"]').count(), 0);
    assert.equal(await page.getByText(/正確答案：/).count(), 0);
    verifyState(state);
  });
  await t.test('寫入403', async child => {
    const { page, state } = await fixture(child, 390);
    await fillProfile(page);
    state.denyMutation = true;
    await page.getByRole('button', { name: '儲存並閱讀教材', exact: true }).click();
    await page.getByRole('heading', { name: '請重新登入', exact: true }).waitFor();
    assert.equal(await page.getByLabel('姓名', { exact: true }).count(), 0);
    assert.equal(await page.locator('.admission-panel, .admission-history').count(), 0);
    verifyState(state);
  });
});

test('撤回後即刷新可重新申請，舊輪成績只可唯讀且不帶入新輪', async t => {
  const { page, state } = await fixture(t, 390);
  await fillProfile(page);
  await completeEducation(page);
  await completeQuiz(page);
  await page.getByRole('button', { name: '前往送出申請', exact: true }).click();
  await page.getByText('撤回本次申請', { exact: true }).click();
  await page.getByRole('button', { name: '確認撤回本次申請', exact: true }).click();
  await page.getByRole('button', { name: '重新開始入社申請', exact: true }).click();
  await page.getByRole('heading', { name: '資料與志願書', exact: true }).waitFor();
  assert.equal(await page.getByLabel('姓名', { exact: true }).inputValue(), '');
  for (const checkbox of await page.getByRole('checkbox').all()) assert.equal(await checkbox.isChecked(), false);
  await page.getByText('歷次入社申請（1 次）', { exact: true }).click();
  await page.getByRole('combobox', { name: '選擇歷次申請', exact: true }).selectOption('isolated-round');
  const history = page.getByRole('region', { name: '歷次入社申請詳情', exact: true });
  await history.getByRole('heading', { name: '第 1 次申請 · 已撤回', exact: true }).waitFor();
  assert.equal(await history.getByText('第 1 次測驗 · 80 分 · 合格', { exact: true }).count(), 1);
  assert.equal(state.round.attempts.length, 0);
  assert.equal(state.round.material_completed_at, null);
  assert.equal(state.round.consented_at, null);
  verifyState(state);
});

for (const hasLegacy of [false, true]) test(`入社費用讀API並隨股數更新，舊資料入口=${hasLegacy}`, async t => {
  const { page, state } = await fixture(t, 390, { hasLegacy });
  const quote = page.locator('dl[aria-label="入社費用估算"]');
  await quote.getByText('$500', { exact: true }).waitFor();
  await quote.getByText('$1,500', { exact: true }).waitFor();
  assert.equal(await page.getByRole('link', { name: '查看舊版入社資料', exact: true }).count(), Number(hasLegacy));
  assert.deepEqual(state.writes, []);
  await page.getByRole('button', { name: '開始入社申請', exact: true }).click();
  await page.getByLabel(/^認購股數/).fill('12');
  await quote.getByText('$1,700', { exact: true }).waitFor();
  await page.getByRole('radio', { name: '全職在校學生', exact: true }).check();
  await quote.getByText('請先填寫有效股數', { exact: true }).waitFor();
  assert.equal(await page.getByRole('button', { name: '儲存並閱讀教材', exact: true }).isEnabled(), false);
  await page.getByLabel(/^認購股數/).fill('5');
  await quote.getByText('$1,000', { exact: true }).waitFor();
  assert.deepEqual(state.writes.map(item => item.path), [`${prefix}/rounds`]);
  verifyState(state);
});

test('費率由API提供，不在瀏覽器寫死入社費或每股金額', async t => {
  const { page, state } = await fixture(t, 390, { admissionFee: 700, shareUnit: 120 });
  const quote = page.locator('dl[aria-label="入社費用估算"]');
  await quote.getByText('$700', { exact: true }).waitFor();
  await quote.getByText('10 股 × $120 = $1,200', { exact: true }).waitFor();
  await quote.getByText('$1,900', { exact: true }).waitFor();
  assert.deepEqual(state.writes, []);
  verifyState(state);
});

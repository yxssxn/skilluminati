// Skilluminati page in Hermes Desktop.
// In plain words: draws the Skilluminati page and the progress strip above the chat. It runs when
// Hermes Desktop loads the plugin. One table lists your skills: tested ones with their verdict, the
// rest by how often you use them, each with a button that starts a test. The words come from copy.py.
//
// Rules: plain ESM, no build step, no JSX; only the Desktop plugin SDK (no document queries, no
// window globals). Every word on the page comes from the backend payload (copy.py), never from here.
// The test button: opens a new chat and sends `/skilluminati <skill>` there (host.newChat + host.composer.submit,
// the documented user-triggered doors). If no composer takes it, the command is copied instead.
// End of a run: the Python side emits plugin.skilluminati.run.done; see onRunDone below.
import { atom, Badge, Button, COMPOSER_AREAS, Tip, Codicon, ErrorState, host, Input, PALETTE_AREA, ROUTES_AREA, SESSION_ROW_AREAS, SIDEBAR_NAV_AREA, Skeleton, useQuery, useValue, writeClipboardText } from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'skilluminati'
const ROUTE = '/skilluminati'
const RUN_DONE = 'plugin.skilluminati.run.done'
const FALLBACK_PROMPT = 'Show my Skilluminati result (skilluminati_result tool).'
const TOP_SKILLS = 8
const KEYS = ['helps', 'no_difference', 'hurts', 'not_enough']

let restRef = null
// The filter box text. An atom (the SDK's state primitive) so the page needs no React import.
const filterText = atom('')
// The run going now, from /live (the same line and numbers as its popup). Polled every few seconds
// while a run goes, slower when idle. Read by the page, the strip above the chat box and the sidebar.
const liveRun = atom(null)
const LIVE_FAST_MS = 3000
const LIVE_SLOW_MS = 10000

const TONES = new Set(['success', 'destructive', 'warn', 'muted'])
// Bar colours: the same families the app's Badge tones use.
const STRIP = { win: 'rgb(16 185 129)', tie: 'color-mix(in srgb, var(--ui-text-tertiary, #888) 45%, transparent)', loss: 'var(--destructive, rgb(229 72 77))' }
// A shape per verdict, so colour is never the only signal.
const SHAPE = { helps: 'arrow-up', no_difference: 'dash', hurts: 'arrow-down', not_enough: 'question' }
const SHAPE_COLOR = { helps: STRIP.win, no_difference: 'var(--ui-text-tertiary)', hurts: STRIP.loss, not_enough: 'rgb(217 119 6)' }
const SECONDARY = { color: 'var(--ui-text-secondary)' }
const TERTIARY = { color: 'var(--ui-text-tertiary)' }
const NUMERIC = { textAlign: 'right', fontVariantNumeric: 'tabular-nums', paddingRight: 8 }
const LIST = { margin: 0, padding: 0, listStyle: 'none' }
const SUMMARY = { cursor: 'pointer', color: 'var(--ui-text-secondary)', fontSize: 12, width: 'fit-content' }

const num = (x) => (typeof x === 'number' && Number.isFinite(x) ? x : null)
const str = (x) => (typeof x === 'string' ? x : '')
const obj = (x) => (x && typeof x === 'object' ? x : {})
// Fill `{name}` slots in a copy string.
const fill = (tpl, vars) => str(tpl).replace(/\{(\w+)\}/g, (m, k) => (vars && vars[k] != null ? String(vars[k]) : m))
const thousands = (n) => (num(n) == null ? '0' : Math.round(n).toLocaleString())
// "11 Nov" in the user's locale; the raw text if it does not parse.
const when = (iso) => {
  const d = new Date(String(iso))
  if (Number.isNaN(d.getTime())) return String(iso || '')
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
}
// Model ids carry a context-size suffix ("claude-x[1m]"); people do not need it.
const modelName = (m) => str(m).replace(/\[[^\]]*\]$/, '')
const sleep = (ms) => new Promise((resolve) => (later ? later(resolve, ms) : resolve()))

// ── Test: start a run in a fresh chat, else copy the command ────────────────
async function startTest(skill, c) {
  const command = `/skilluminati ${skill}`
  if (typeof host.newChat === 'function' && host.composer && typeof host.composer.submit === 'function') {
    try {
      host.newChat()
      for (let i = 0; i < 16; i++) {
        await sleep(250)
        if (host.composer.submit('new', command) === true) {
          host.notify({ kind: 'success', message: fill(c.test_started, { skill }) })
          return
        }
      }
    } catch (e) {
      // fall through to the copy
    }
  }
  let copied = false
  try {
    await writeClipboardText(command)
    copied = true
  } catch (e) {
    copied = false
  }
  host.notify({ kind: copied ? 'info' : 'warning', message: fill(copied ? c.test_copied : c.test_failed, { command }) })
}

function TestButton({ skill, label, c, busy }) {
  return jsx(Button, {
    size: 'sm',
    variant: 'outline',
    disabled: busy,
    title: busy ? str(c.test_busy) : `/skilluminati ${skill}`,
    onClick: () => startTest(skill, c),
    style: { whiteSpace: 'nowrap' },
    children: label
  })
}

const Shape = ({ k }) => jsx('span', { 'aria-hidden': true, style: { display: 'inline-flex', color: SHAPE_COLOR[k] }, children: jsx(Codicon, { name: SHAPE[k] || 'question', size: '0.8rem' }) })

function VerdictWord({ k, c }) {
  const tone = obj(c.verdict_tones)[k]
  return jsx(Tip, { label: str(obj(c.verdict_meaning)[k]), children: jsx('span', {
    style: { cursor: 'help' },
    children: jsx(Badge, {
      variant: TONES.has(tone) ? tone : 'muted',
      className: 'text-xs',
      children: jsxs('span', { style: { display: 'inline-flex', alignItems: 'center', gap: 4 }, children: [jsx(Shape, { k }), str(obj(c.verdict_words)[k]) || k] })
    })
  }) })
}

// The small second word: how sure, or (for "not enough to tell") which way it leans. Tip on hover.
function Qualifier({ r, c }) {
  let word = ''
  let tip = ''
  if (r.strength) {
    word = str(obj(c.strength_words)[r.strength])
    tip = str(obj(c.strength_tips)[r.strength])
  } else if (r.key === 'not_enough' && r.lean) {
    word = str(obj(c.lean_words)[r.lean])
    tip = str(c.lean_tip)
  }
  if (!word) return null
  return jsx(Tip, { label: tip, children: jsx('span', { style: { fontSize: 11.5, ...TERTIARY, cursor: tip ? 'help' : undefined, textDecoration: tip ? 'underline dotted' : 'none', textUnderlineOffset: 3 }, children: word }) })
}

// The signature: one bar, green for better, grey for same, red for worse.
function ScoreStrip({ r, label, width }) {
  const total = (r.wins || 0) + (r.ties || 0) + (r.losses || 0)
  if (!total) return null
  const seg = (n, color, key) => (n ? jsx('span', { style: { flexGrow: n, flexBasis: 0, background: color } }, key) : null)
  return jsxs('span', {
    role: 'img',
    'aria-label': label,
    title: label,
    style: { display: 'flex', flexShrink: 0, gap: 2, height: 6, width, borderRadius: 2, overflow: 'hidden' },
    children: [seg(r.wins, STRIP.win, 'w'), seg(r.ties, STRIP.tie, 't'), seg(r.losses, STRIP.loss, 'l')]
  })
}

function Working({ r, c }) {
  const w = obj(r.working)
  const tasks = Array.isArray(w.tasks) ? w.tasks : []
  const rows = obj(c.working_task_row)
  const lines = []
  lines.push(str(obj(c.verdict_meaning)[r.key]))
  if (w.partial) lines.push(str(c.working_partial))
  if (r.date || r.model) lines.push(fill(c.result_when, { when: when(r.date), model: modelName(r.model) || c.model_unknown }))
  if (r.strength && obj(c.strength_words)[r.strength]) {
    lines.push(fill(c.working_strength, { word: obj(c.strength_words)[r.strength], tip: str(obj(c.strength_tips)[r.strength]) }))
  }
  // counts: nested under `counts` or flat on the row.
  const k = r.counts && typeof r.counts === 'object' ? r.counts : r
  if (num(k.tested) != null) lines.push(fill(c.working_counts, { uses: thousands(k.uses), usable: thousands(k.usable), tested: thousands(k.tested) }))
  lines.push(num(r.low) != null && num(r.high) != null ? fill(c.working_range, { low: r.low, high: r.high }) : str(c.working_range_none))
  if (w.order && num(w.order.n)) {
    lines.push(fill(c.working_order, { flips: w.order.flips || 0, n: w.order.n }) + (num(w.bias_pct) != null ? ` ${fill(c.working_order_bias, { pct: w.bias_pct })}` : ''))
  }
  if (w.noise) {
    lines.push(fill(c.working_noise, { n: (w.noise.wins || 0) + (w.noise.losses || 0) + (w.noise.ties || 0), wins: w.noise.wins, losses: w.noise.losses, ties: w.noise.ties, fair: w.noise.noisy ? c.working_noise_unfair : c.working_noise_fair }))
  }
  if (num(w.calls)) lines.push(fill(c.working_cost, { calls: thousands(w.calls), tokens: thousands(w.tokens) }))
  if (w.judge) lines.push(fill(c.working_judge, { judge: modelName(w.judge) }))
  const run = str(w.run) || str(r.run)
  if (run) lines.push(fill(c.working_run, { run }))
  return jsxs('div', {
    style: { display: 'flex', flexDirection: 'column', gap: 10, padding: '10px 12px', marginTop: 6, borderRadius: 4, background: 'var(--ui-bg-quaternary)', fontSize: 12.5, lineHeight: 1.5 },
    children: [
      tasks.length
        ? jsxs('div', {
            children: [
              jsx('div', { style: { fontWeight: 600, marginBottom: 4 }, children: str(c.working_tasks) }),
              jsx('ol', {
                style: { ...LIST, display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(190px, 1fr))', gap: '2px 16px' },
                children: tasks.map((t, i) =>
                  jsxs('li', {
                    style: { display: 'flex', alignItems: 'center', gap: 6 },
                    children: [
                      jsx('span', { style: { width: 7, height: 7, borderRadius: 2, flexShrink: 0, background: STRIP[t] || STRIP.tie } }),
                      jsx('span', { style: { ...SECONDARY, minWidth: 48 }, children: fill(c.working_task_label, { i: i + 1 }) }),
                      jsx('span', { children: str(rows[t]) || t })
                    ]
                  }, i)
                )
              })
            ]
          })
        : jsx('p', { style: { margin: 0, ...SECONDARY }, children: str(c.working_none) }),
      jsx('div', { style: { display: 'flex', flexDirection: 'column', gap: 6, maxWidth: '72ch', ...SECONDARY }, children: lines.filter(Boolean).map((l, i) => jsx('p', { style: { margin: 0, color: i === 0 ? 'var(--ui-text-primary)' : undefined }, children: l }, i)) })
    ]
  })
}

// ── Top of the page: the one-line answer, and a slim line while a test runs ──
// The verdict count, one per line. Hover a line for its meaning.
function Summary({ results, untested, c }) {
  const parts = obj(c.summary_parts)
  const items = []
  for (const k of KEYS) {
    const n = results.filter((r) => r.key === k).length
    const forms = Array.isArray(parts[k]) ? parts[k] : []
    if (n && forms.length) {
      items.push(jsx('li', { children: jsx(Tip, { label: str(obj(c.verdict_meaning)[k]), children: jsxs('span', { style: { display: 'inline-flex', alignItems: 'center', gap: 6, cursor: 'help' }, children: [jsx(Shape, { k }), fill(forms[n === 1 ? 0 : 1], { n })] }) }) }, k))
    }
  }
  if (untested) items.push(jsx('li', { style: TERTIARY, children: fill(c.summary_untested, { n: untested }) }, 'untested'))
  return jsx('ul', { style: { ...LIST, display: 'flex', flexDirection: 'column', gap: 4, fontSize: 14, fontWeight: 500 }, children: items })
}

function Running({ run, c }) {
  const known = num(run.done) != null && num(run.total)
  const share = known ? Math.min(1, run.done / run.total) : null
  const line = str(run.line)
  return jsxs('div', {
    'aria-live': 'polite',
    style: { display: 'flex', alignItems: 'center', gap: 8, fontSize: 12.5, ...SECONDARY },
    children: [
      jsx(Codicon, { name: 'loading', spinning: true, size: '0.8rem' }),
      jsx('span', { children: fill(c.running_title, { skill: run.skill || str(c.running_selftest) }) }),
      jsx('span', { style: TERTIARY, children: line || (known ? fill(c.running_progress, run) : str(c.running_unknown)) }),
      known
        ? jsx('span', {
            style: { width: 64, height: 3, borderRadius: 2, background: 'var(--ui-stroke-secondary)', overflow: 'hidden' },
            children: jsx('span', { style: { display: 'block', height: '100%', transformOrigin: 'left', transform: `scaleX(${share})`, background: STRIP.win, transition: 'transform 400ms ease' } })
          })
        : null
    ]
  })
}

// ── The one table: a row per skill ───────────────────────────────────────────
const GRID = { display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 96px 96px 230px 96px', alignItems: 'center', gap: 12 }

function SkillHead({ c }) {
  const h = obj(c.skills_head)
  const tips = obj(c.skills_head_tips)
  return jsxs('div', {
    style: { ...GRID, padding: '0 0 6px', fontSize: 11.5, lineHeight: 1.3, whiteSpace: 'nowrap', ...TERTIARY, borderBottom: '1px solid var(--ui-stroke-secondary)' },
    children: [jsx('span', { children: str(h.name) }), jsx('span', { style: NUMERIC, children: str(h.uses) }), jsx(Tip, { label: str(tips.tokens), children: jsx('span', { style: { ...NUMERIC, cursor: 'help' }, children: str(h.tokens) }) }), jsx('span', { children: str(h.verdict) }), jsx('span', {})]
  })
}

// "Better 7 · Same 4 · Worse 1", each part with its own hover tip.
function Counts({ r, c }) {
  const words = obj(c.legend)
  const tips = obj(c.legend_tips)
  const parts = [['win', r.wins], ['tie', r.ties], ['loss', r.losses]]
  return jsx('span', {
    style: { display: 'inline-flex', gap: 6, fontSize: 11.5, ...SECONDARY, fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' },
    children: parts.map(([k, n], i) => jsx(Tip, { label: str(tips[k]), children: jsx('span', { style: { cursor: 'help' }, children: `${i ? '· ' : ''}${str(words[k])} ${num(n) || 0}` }) }, k))
  })
}

function ResultCell({ x, c, run }) {
  const r = x.r
  if (run && run.skill === x.name) {
    return jsxs('span', { style: { display: 'flex', alignItems: 'center', gap: 6, ...SECONDARY }, children: [jsx(Codicon, { name: 'loading', spinning: true, size: '0.8rem' }), num(run.done) != null && num(run.total) ? `${run.done} of ${run.total}` : ''] })
  }
  if (!r) return jsx('span', { style: TERTIARY, children: str(c.not_tested) })
  return jsxs('span', {
    style: { display: 'flex', flexDirection: 'column', gap: 5, minWidth: 0 },
    children: [
      jsxs('span', { style: { display: 'flex', alignItems: 'center', gap: 8 }, children: [jsx(VerdictWord, { k: r.key, c }), jsx(Qualifier, { r, c })] }),
      jsxs('span', {
        style: { display: 'flex', alignItems: 'center', gap: 8 },
        children: [jsx(ScoreStrip, { r, label: fill(c.result_bar, r), width: 56 }), jsx(Counts, { r, c })]
      })
    ]
  })
}

function SkillRow({ x, c, busy, run }) {
  const s = x.s || {}
  return jsxs('li', {
    style: { padding: '10px 0', borderBottom: '1px solid var(--ui-stroke-secondary)', fontSize: 13 },
    children: [
      jsxs('div', {
        style: GRID,
        children: [
          jsxs('span', {
            style: { display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 },
            children: [
              jsx('span', { translate: 'no', title: x.name, style: { fontWeight: x.r ? 600 : 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, children: x.name }),
              (x.r ? x.r.source === 'pack' : s.pack) ? jsx(Tip, { label: str(c.pack_tip), children: jsx(Badge, { variant: 'outline', size: 'xs', children: str(c.pack_mark) }) }) : null
            ]
          }),
          jsx('span', { style: { ...SECONDARY, ...NUMERIC }, children: s.uses ? thousands(s.uses) : str(c.used_never) }),
          x.s
            ? jsx(Tip, { label: fill(c.tokens_tip, { n: thousands(s.prompt_tokens), load: thousands(s.load_tokens) }), children: jsx('span', { style: { ...TERTIARY, ...NUMERIC, whiteSpace: 'nowrap', cursor: 'help' }, children: fill(c.tokens_per_prompt, { n: thousands(s.load_tokens) }) }) })
            : jsx('span', {}),
          jsx(ResultCell, { x, c, run }),
          jsx('span', { style: { justifySelf: 'end' }, children: run && run.skill === x.name ? null : jsx(TestButton, { skill: x.name, label: str(x.r ? c.test_again : c.test_button), c, busy }) })
        ]
      }),
      // Native disclosure: keyboard and screen-reader friendly, no state to keep.
      x.r ? jsxs('details', { style: { marginTop: 4 }, children: [jsx('summary', { style: SUMMARY, children: str(c.show_working) }), jsx(Working, { r: x.r, c })] }) : null
    ]
  })
}

// Tested skills first, then by how often you use them. A result whose skill folder is gone still shows.
function tableRows(skills, results) {
  const byName = new Map(results.map((r) => [r.skill, r]))
  const rows = skills.map((s) => ({ name: s.name, s, r: byName.get(s.name) || null }))
  const listed = new Set(skills.map((s) => s.name))
  for (const r of results) if (!listed.has(r.skill)) rows.push({ name: r.skill, s: null, r })
  const uses = (x) => (x.s && num(x.s.uses)) || 0
  return rows.sort((a, b) => (b.r ? 1 : 0) - (a.r ? 1 : 0) || uses(b) - uses(a))
}

function SkillTable({ rows, c, busy, run }) {
  const q = useValue(filterText)
  const needle = str(q).trim().toLowerCase()
  const row = (x) => jsx(SkillRow, { x, c, busy, run }, x.name)
  const shown = needle ? rows.filter((x) => x.name.toLowerCase().includes(needle)) : rows.slice(0, TOP_SKILLS)
  const rest = needle ? [] : rows.slice(TOP_SKILLS)
  return jsxs('section', {
    style: { display: 'flex', flexDirection: 'column', gap: 10 },
    children: [
      jsxs('div', {
        style: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap' },
        children: [
          jsx('div', {
            style: { width: 220 },
            children: jsx(Input, { type: 'search', value: str(q), placeholder: str(c.skills_filter), 'aria-label': str(c.skills_filter), autoComplete: 'off', spellCheck: false, onChange: (e) => filterText.set(e.target.value) })
          })
        ]
      }),
      jsx(SkillHead, { c }),
      shown.length
        ? jsx('ul', { style: LIST, children: shown.map(row) })
        : jsx('p', { style: { margin: 0, fontSize: 13, ...SECONDARY }, children: needle ? fill(c.skills_no_match, { q: str(q).trim() }) : str(c.skills_none) }),
      rest.length
        ? jsxs('details', {
            children: [
              jsx('summary', { style: { ...SUMMARY, marginTop: 4 }, children: fill(c.skills_show_all, { n: rows.length }) }),
              jsx('ul', { style: LIST, children: rest.map(row) })
            ]
          })
        : null
    ]
  })
}

function Page({ d }) {
  const c = obj(d.copy)
  const results = Array.isArray(d.results) ? d.results.filter((r) => r && typeof r.skill === 'string') : []
  const skills = Array.isArray(d.skills) ? d.skills.filter((s) => s && typeof s.name === 'string') : []
  const live = useValue(liveRun)
  const run = live || (d.running && typeof d.running === 'object' ? d.running : null)
  const rows = tableRows(skills, results)
  const untested = rows.filter((x) => !x.r && !(run && run.skill === x.name)).length
  return jsxs('div', {
    style: { display: 'flex', flexDirection: 'column', gap: 20 },
    children: [
      jsxs('div', {
        style: { display: 'flex', flexDirection: 'column', gap: 8 },
        children: [
          results.length ? jsx(Summary, { results, untested, c }) : jsx('p', { style: { margin: 0, fontSize: 15, fontWeight: 500 }, children: str(c.page_empty) }),
          run ? jsx(Running, { run, c }) : null
        ]
      }),
      jsx(SkillTable, { rows, c, busy: Boolean(run), run })
    ]
  })
}

function SkilluminatiPage() {
  const q = useQuery({
    queryKey: [ID, 'page'],
    queryFn: () => restRef('/page'),
    refetchInterval: (query) => (query && query.state && query.state.data && query.state.data.running ? 5000 : 30000)
  })
  const c = (q.data && q.data.copy) || {}
  let body
  if (q.isLoading) body = jsx(Skeleton, { className: 'h-40 w-full' })
  else if (q.error || !q.data) {
    body = jsx(ErrorState, {
      title: str(c.load_error) || 'Could not load the page data',
      description: `${String(q.error && q.error.message ? q.error.message : q.error || '')} ${str(c.load_error_hint)}`.trim()
    })
  } else body = jsx(Page, { d: q.data })
  return jsx('div', {
    style: { height: '100%', overflow: 'auto' },
    children: jsxs('div', {
      style: { maxWidth: 900, margin: '0 auto', padding: '28px 28px 48px', display: 'flex', flexDirection: 'column', gap: 18, color: 'var(--ui-text-primary)' },
      children: [
        jsxs('header', {
          style: { display: 'flex', flexDirection: 'column', gap: 6 },
          children: [
            jsx('h1', { style: { margin: 0, fontSize: 20, fontWeight: 600, letterSpacing: '-0.01em' }, children: str(c.page_title) || 'Skilluminati' }),
            c.page_what ? jsx('p', { style: { margin: 0, fontSize: 13.5, lineHeight: 1.5, ...SECONDARY }, children: c.page_what }) : null
          ]
        }),
        body
      ]
    })
  })
}

// ── Activity outside the page: a strip above the chat box, a spinner on the chat's sidebar row ──
function isMine(run, sessionId) {
  return Boolean(run) && (!run.session_id || run.session_id === sessionId)
}

function ChatStrip() {
  const run = useValue(liveRun)
  const focused = useValue(host.state.focusedStoredSessionId)
  if (!isMine(run, focused)) return null
  return jsxs('div', {
    'aria-live': 'polite',
    style: { display: 'flex', alignItems: 'center', gap: 8, padding: '6px 12px', fontSize: 12, ...SECONDARY },
    children: [
      jsx(Codicon, { name: 'loading', spinning: true, size: '0.8rem' }),
      jsx('span', { style: { fontWeight: 500, color: 'var(--ui-text-primary)' }, children: 'Skilluminati' }),
      jsx('span', { style: { minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, children: str(run.line) || str(run.skill) }),
      jsx('button', {
        type: 'button',
        onClick: () => host.navigate(ROUTE),
        style: { marginLeft: 'auto', background: 'none', border: 0, padding: 0, cursor: 'pointer', fontSize: 12, textDecoration: 'underline', ...SECONDARY },
        children: 'Open page'
      }),
      jsx('button', {
        type: 'button',
        onClick: () => { submit(focused, '/skilluminati stop') },
        style: { background: 'none', border: 0, padding: 0, cursor: 'pointer', fontSize: 12, textDecoration: 'underline', ...SECONDARY },
        children: 'Stop'
      })
    ]
  })
}

// The same travelling ring Hermes draws round a working chat in the sidebar (its own `arc-border
// arc-row` classes). The row is the nearest positioned box, so the ring wraps the row.
function RowSpinner({ sessionId }) {
  const run = useValue(liveRun)
  if (!run || !run.session_id || run.session_id !== sessionId) return null
  return jsx('span', { 'aria-hidden': 'true', className: 'arc-border arc-row', title: str(run.line) })
}

async function pollLive() {
  if (!restRef) return
  let next = LIVE_SLOW_MS
  try {
    const d = await restRef('/live')
    const run = d && d.running && typeof d.running === 'object' ? d.running : null
    liveRun.set(run)
    if (run) next = LIVE_FAST_MS
  } catch (e) {
    liveRun.set(null)
  }
  if (later) later(pollLive, next)
}

// One submit per finished run, even if the event arrives twice.
const handled = new Set()
// Waits for a busy chat to go idle; cleared on unload so a reload leaves nothing behind.
// ctx.setTimeout (tracked, cleared on unload), set in register().
let later = null

function payloadOf(event) {
  const p = (event && event.payload) || {}
  return {
    run: typeof p.run === 'string' ? p.run : '',
    sessionId: typeof p.session_id === 'string' && p.session_id ? p.session_id : null,
    prompt: typeof p.prompt === 'string' && p.prompt.trim() ? p.prompt.trim().slice(0, 200) : FALLBACK_PROMPT
  }
}

// Is the run's chat the one on screen, and is it mid-turn? `sessionId` is the stored id the
// gateway knows. Only the focused chat can be matched to its live turn flag, so any other chat
// counts as "not on screen" and gets the button, never an automatic send.
function chatState(sessionId) {
  if (!sessionId || host.state.focusedStoredSessionId.get() !== sessionId) return 'away'
  return host.state.busy.get() ? 'busy' : 'idle'
}

function submit(sessionId, prompt) {
  try {
    return host.composer.submit(sessionId, prompt) === true
  } catch (e) {
    return false
  }
}

// Show in chat: bring the run's chat to the front first (the button did nothing
// while the Skilluminati page covered the chat), then send the line once that chat is idle.
async function showInChat(sessionId, prompt) {
  try {
    if (sessionId && host.state.focusedStoredSessionId.get() !== sessionId && typeof host.openSession === 'function') {
      await host.openSession(sessionId)
    } else if (!sessionId && typeof host.newChat === 'function') {
      host.newChat()
    }
  } catch (e) {
    // fall through: try the chat that is on screen
  }
  for (let i = 0; i < 24; i++) {
    await sleep(250)
    const state = chatState(sessionId)
    if (state === 'idle' && submit(sessionId, prompt)) return
    if (!sessionId && !host.state.busy.get() && submit('new', prompt)) return
  }
  host.notify({ kind: 'warning', message: 'Skilluminati: that chat is busy. Your result is on the page.' })
  host.navigate(ROUTE)
}

// One line, two buttons. Never sends on its own.
function offer(sessionId, prompt) {
  host.notify({
    kind: 'success',
    message: 'Skilluminati: your result is ready.',
    durationMs: 60000,
    action: { label: 'Show in chat', onClick: () => { showInChat(sessionId, prompt) } },
    secondaryAction: { label: 'Open page', onClick: () => host.navigate(ROUTE) }
  })
}

function onRunDone(event) {
  liveRun.set(null)  // the strip and the sidebar spinner go away at once
  const { run, sessionId, prompt } = payloadOf(event)
  if (!run || handled.has(run)) return
  handled.add(run)
  // Never sends on its own: the result line goes into the chat only when you click "Show in chat"
  // (a plugin must not start a chat turn without a click).
  offer(sessionId, prompt)
}

export default {
  id: ID,
  name: 'Skilluminati',
  defaultEnabled: false,
  register(ctx) {
    restRef = ctx.rest
    later = ctx.setTimeout
    ctx.onDispose(() => {
      restRef = null
      later = null
      filterText.set('')
      liveRun.set(null)
    })
    ctx.onEvent(RUN_DONE, onRunDone)
    later(pollLive, 0)
    ctx.registerMany([
      { id: 'page', area: ROUTES_AREA, data: { path: ROUTE }, render: () => jsx(SkilluminatiPage, {}) },
      { id: 'nav', area: SIDEBAR_NAV_AREA, data: { path: ROUTE, label: 'Skilluminati', codicon: 'book' } },
      { id: 'chat-strip', area: COMPOSER_AREAS.top, render: () => jsx(ChatStrip, {}) },
      { id: 'row-spinner', area: SESSION_ROW_AREAS.leading, data: { render: (p) => jsx(RowSpinner, { sessionId: p.sessionId }) } },
      {
        id: 'open',
        area: PALETTE_AREA,
        data: { id: 'skilluminati.open', label: 'Open Skilluminati', run: () => host.navigate(ROUTE) }
      }
    ])
  }
}

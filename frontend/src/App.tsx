import { useEffect, useMemo, useState } from 'react'

type RunStatus = 'queued' | 'running' | 'awaiting_approval' | 'blocked' | 'completed' | 'failed'

type Run = {
  run_id: string
  case_id: string
  tenant_id: string
  status: RunStatus
  started_at: string
  finished_at: string | null
  state: Record<string, unknown>
  is_public_demo: boolean
  replay_of: string | null
}

type RunEvent = {
  event_id: string
  event_type: string
  node: string
  message: string
  timestamp: string
  payload: Record<string, unknown>
}

type Metrics = {
  total_runs: number
  success_rate: number
  error_rate: number
  p95_latency_ms: number
  average_model_cost_usd: number
  recovery_rate: number
  unsafe_writes: number
  duplicate_side_effects: number
  source: string
}

const fallbackMetrics: Metrics = {
  total_runs: 0,
  success_rate: 0,
  error_rate: 0,
  p95_latency_ms: 0,
  average_model_cost_usd: 0,
  recovery_rate: 0,
  unsafe_writes: 0,
  duplicate_side_effects: 0,
  source: 'seeded-demo',
}

const nodeNames: Record<string, string> = {
  intake_validation: 'Intake validated',
  authoritative_record_collection: 'Records collected',
  policy_retrieval: 'Policy retrieved',
  conflict_freshness_checks: 'Integrity checked',
  deterministic_policy_decision: 'Policy decision',
  llm_recommendation: 'Model recommendation',
  human_interrupt: 'Human approval',
  idempotent_execution: 'Effect executed',
  outcome_verification: 'Outcome verified',
  audit_closure: 'Audit closed',
  blocked_closure: 'Stopped safely',
}

const statusLabel: Record<RunStatus, string> = {
  queued: 'Queued',
  running: 'Running',
  awaiting_approval: 'Awaiting approval',
  blocked: 'Blocked safely',
  completed: 'Verified',
  failed: 'Failed',
}

async function api<T>(path: string): Promise<T> {
  const response = await fetch(path)
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`)
  return response.json() as Promise<T>
}

function pct(value: number) {
  return `${(value * 100).toFixed(0)}%`
}

function time(value: string | null) {
  if (!value) return '—'
  return new Intl.DateTimeFormat('en', { hour: 'numeric', minute: '2-digit', second: '2-digit' }).format(
    new Date(value),
  )
}

function MetricCard({ label, value, detail, tone = 'neutral' }: { label: string; value: string; detail: string; tone?: string }) {
  return (
    <article className={`metric metric--${tone}`}>
      <div className="metric__label">{label}</div>
      <div className="metric__value">{value}</div>
      <div className="metric__detail">{detail}</div>
    </article>
  )
}

function App() {
  const [runs, setRuns] = useState<Run[]>([])
  const [metrics, setMetrics] = useState<Metrics>(fallbackMetrics)
  const [selectedId, setSelectedId] = useState<string>('')
  const [events, setEvents] = useState<RunEvent[]>([])
  const [error, setError] = useState('')

  useEffect(() => {
    Promise.all([api<Run[]>('/v1/runs'), api<Metrics>('/v1/metrics/summary')])
      .then(([nextRuns, nextMetrics]) => {
        setRuns(nextRuns)
        setMetrics(nextMetrics)
        setSelectedId((current) => current || nextRuns[0]?.run_id || '')
      })
      .catch((reason: Error) => setError(reason.message))
  }, [])

  const selected = useMemo(() => runs.find((run) => run.run_id === selectedId) ?? null, [runs, selectedId])

  useEffect(() => {
    if (!selected) return
    const source = new EventSource(`/v1/runs/${selected.run_id}/events`)
    const receive = (message: MessageEvent<string>) => {
      const event = JSON.parse(message.data) as RunEvent
      setEvents((current) => (current.some((item) => item.event_id === event.event_id) ? current : [...current, event]))
    }
    source.addEventListener('demo', receive as EventListener)
    source.addEventListener('node', receive as EventListener)
    source.addEventListener('approval', receive as EventListener)
    source.addEventListener('effect', receive as EventListener)
    source.addEventListener('closure', receive as EventListener)
    source.onerror = () => source.close()
    return () => source.close()
  }, [selected])

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand__mark">CP</div>
          <div>
            <div className="brand__name">Control Plane</div>
            <div className="brand__sub">Agent reliability</div>
          </div>
        </div>
        <nav aria-label="Primary navigation">
          <a className="navitem navitem--active" href="#overview"><span>01</span>Overview</a>
          <a className="navitem" href="#runs"><span>02</span>Case runs</a>
          <a className="navitem" href="#evidence"><span>03</span>Evidence chain</a>
        </nav>
        <div className="sidebar__foot">
          <div className="environment"><i /> Synthetic environment</div>
          <p>No real customers, policies, or financial records.</p>
          <a href="/docs">OpenAPI reference ↗</a>
        </div>
      </aside>

      <main>
        <header className="topbar">
          <div>
            <p className="eyebrow">Regulated financial workflow</p>
            <h1>Reliability operations</h1>
          </div>
          <div className="topbar__right">
            <span className="live"><i /> Systems nominal</span>
            <span className="viewer">Public viewer</span>
          </div>
        </header>

        <section className="content" id="overview">
          <div className="notice">
            <div className="notice__icon">i</div>
            <div><strong>Precomputed public evidence</strong><span>Mutations require verified operator or approver claims.</span></div>
            <span className="notice__source">{metrics.source}</span>
          </div>

          {error && <div className="error">API unavailable: {error}</div>}

          <div className="metrics">
            <MetricCard label="Task success" value={pct(metrics.success_rate)} detail={`${metrics.total_runs} inspected runs`} tone="good" />
            <MetricCard label="Failure recovery" value={pct(metrics.recovery_rate)} detail="Injected transient failures" tone="blue" />
            <MetricCard label="p95 latency" value={`${(metrics.p95_latency_ms / 1000).toFixed(2)}s`} detail="Excludes human wait" />
            <MetricCard label="Unsafe effects" value={String(metrics.unsafe_writes)} detail="Authorization violations" tone="good" />
            <MetricCard label="Model cost" value={`$${metrics.average_model_cost_usd.toFixed(4)}`} detail="Average per case" />
          </div>

          <div className="workspace" id="runs">
            <section className="panel runlist">
              <div className="panel__head">
                <div><p className="eyebrow">Synthetic case activity</p><h2>Run ledger</h2></div>
                <span className="count">{runs.length}</span>
              </div>
              <div className="runlist__body">
                {runs.map((run) => (
                  <button
                    className={`runrow ${run.run_id === selectedId ? 'runrow--selected' : ''}`}
                    key={run.run_id}
                    onClick={() => {
                      setEvents([])
                      setSelectedId(run.run_id)
                    }}
                  >
                    <span className={`statusdot statusdot--${run.status}`} />
                    <span className="runrow__main">
                      <strong>{String(run.state.demo_name ?? run.case_id.slice(0, 12)).replaceAll('-', ' ')}</strong>
                      <small>{run.run_id.slice(0, 8)} · {time(run.started_at)}</small>
                    </span>
                    <span className={`status status--${run.status}`}>{statusLabel[run.status]}</span>
                  </button>
                ))}
              </div>
            </section>

            <section className="panel detail" id="evidence">
              {selected ? (
                <>
                  <div className="panel__head detail__head">
                    <div><p className="eyebrow">Evidence chain</p><h2>{String(selected.state.demo_name ?? 'Case run').replaceAll('-', ' ')}</h2></div>
                    <span className={`status status--${selected.status}`}>{statusLabel[selected.status]}</span>
                  </div>
                  <div className="detail__meta">
                    <div><span>Tenant</span><strong>{selected.tenant_id}</strong></div>
                    <div><span>Run</span><strong>{selected.run_id.slice(0, 13)}</strong></div>
                    <div><span>Started</span><strong>{time(selected.started_at)}</strong></div>
                    <div><span>Retries</span><strong>{String(selected.state.retry_count ?? 0)}</strong></div>
                  </div>
                  {selected.state.blocked_reason && (
                    <div className="safe-stop"><strong>Safe stop</strong><span>{String(selected.state.blocked_reason)}</span></div>
                  )}
                  <ol className="timeline">
                    {events.map((event, index) => (
                      <li key={event.event_id}>
                        <span className="timeline__index">{String(index + 1).padStart(2, '0')}</span>
                        <span className="timeline__rail" />
                        <div><strong>{nodeNames[event.node] ?? event.node}</strong><p>{event.message}</p></div>
                        <time>{time(event.timestamp)}</time>
                      </li>
                    ))}
                  </ol>
                  <div className="integrity">
                    <span>Audit integrity</span>
                    <strong><i /> Evidence → policy → approval → effect → verification</strong>
                  </div>
                </>
              ) : (
                <div className="empty">Select a run to inspect its evidence chain.</div>
              )}
            </section>
          </div>
        </section>
      </main>
    </div>
  )
}

export default App

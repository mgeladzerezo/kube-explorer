# Kube Explorer

A read-only Kubernetes dashboard that shows workloads, pods, events and logs across
namespaces as they change, highlights what is failing, and can ask Claude why. For a
failing workload it collects the evidence an engineer would look at, strips credentials
from it, and returns ranked hypotheses, each with the evidence it rests on and one
read-only `kubectl` command to confirm it.

It comes with a library of 20 reproducible faults and a harness that scores the diagnoser
against them.

## Architecture

```
browser ── WebSocket /ws/cluster ──┐
        ── REST /api/* ────────────┤
                                   ▼
                      FastAPI backend (one process)
        ┌─────────────────────────────────────────────────┐
        │ watcher threads ─► ClusterView ─► Hub ─► sockets │   list + watch (read-only)
        │   (one per resource)                             │◄──────────────── Kubernetes API
        │                                                  │
        │ POST /api/diagnoses                              │
        │   EvidenceCollector ─► redact ─► prompt ─► model │──► Anthropic API
        │                                    parse/validate│
        └──────────────────────────┬──────────────────────┘
                                   ▼
                        PostgreSQL (diagnoses, eval runs)
```

| Path | What it is |
| --- | --- |
| `backend/app/kube/` | `ClusterReader` interface; `real.py` (official client), `fake.py` (recorded objects), `status.py` (pod/workload health) |
| `backend/app/watch/` | `watcher.py` list-then-watch loop, `view.py` current state and change messages, `hub.py` fan-out, `manager.py` thread-to-event-loop bridge |
| `backend/app/diagnosis/` | `evidence.py`, `redact.py`, `prompt.py`, `llm.py` (Anthropic SDK), `schema.py` (output validation), `baseline.py` (fake model) |
| `backend/app/eval/` | Scenario loader, runner, `python -m app.eval` |
| `frontend/` | React + TypeScript + Vite + Tailwind; TanStack Query for logs and diagnosis requests |
| `faults/` | One directory per fault: `inject.yaml`, `revert.sh`, `scenario.yaml`, `captured/` |
| `deploy/helm/kube-explorer/` | Chart: ServiceAccount, read-only ClusterRole, ExternalSecret, NetworkPolicies, migration hook |
| `deploy/kind/` | Three-node kind config and install script |
| `deploy/terraform/` | ECR repositories and the Secrets Manager secret |

### Watch to WebSocket

The Kubernetes client is blocking, so each watched resource (pods, deployments,
statefulsets, daemonsets, events) gets a thread running `ResourceWatcher`:

1. List the resource, emit the result as a `Replace`, remember the list's `resourceVersion`.
2. Watch from that version. Every event advances the remembered version; bookmarks advance
   it without changing anything.
3. If the stream ends (server timeout) or the connection drops, watch again from the last
   version. Nothing is missed and nothing is replayed. Errors back off exponentially.
4. If the server answers 410 Gone, the version is too old: go back to step 1. The fresh
   `Replace` lets the view delete objects that disappeared in the gap.

Changes are handed to the event loop with `call_soon_threadsafe`, so `ClusterView` and `Hub`
are only ever touched by one thread and need no locks. The view turns objects into small
summaries and publishes `upsert` / `delete` messages only when a summary actually changes.

A browser gets a full `snapshot` on connect and deltas after it. Subscribing and taking the
snapshot happen without an `await` in between, so no change can fall into the gap. Each
subscriber has a bounded queue; one that falls behind is closed with code 1013 and reconnects
for a new snapshot. The frontend reconnects with capped exponential backoff, and a snapshot
always replaces its state.

### Diagnosis

`EvidenceCollector` gathers, for a Deployment, StatefulSet, DaemonSet or Pod:

- the workload object and up to three pods (failing ones first), trimmed of bookkeeping fields
- events for the workload, its ReplicaSets, pods and PVCs (warnings first)
- current logs, plus the previous instance's logs for containers that have restarted
- Services in the namespace, with whether their selector matches the pod template, whether
  each `targetPort` is a port the containers declare, and ready / not-ready endpoint counts
- ConfigMap and Secret references, with existence and key presence checked for ConfigMaps
- referenced PVCs, NetworkPolicies and ResourceQuotas in the namespace
- node labels, taints and allocatable resources, only when a pod is unschedulable

The model is called through the Anthropic Python SDK with `output_config.format` set to a
JSON schema, so the answer is schema-constrained. The answer is still validated with
Pydantic (`schema.py`): the category must be one of a closed set of root causes, confidence
must be within 0..1, every hypothesis must cite evidence, and the next command must be a
single line. Hypotheses are ordered by rank and renumbered. A command that is not a plain
read-only `kubectl` call is kept but flagged, and the UI shows a warning next to it. An
unusable answer is retried once and then reported as HTTP 502.

Model id, effort and token limit come from `ANTHROPIC_MODEL` (default `claude-opus-5-5`),
`ANTHROPIC_EFFORT` and `ANTHROPIC_MAX_TOKENS`. `ANTHROPIC_REFUSAL_FALLBACK=true` (default)
sends the request through the server-side fallback beta so a request declined by a safety
classifier is retried on Anthropic's recommended fallback model.

### Redaction

Nothing leaves the backend unredacted: evidence, the log endpoint and event messages on the
WebSocket all pass through `redact.py`.

- The ClusterRole has no access to Secrets. A Secret value can therefore only appear
  indirectly: in logs, in literal env values, in arguments, or in annotations.
- Structural rules: literal env values with sensitive names are replaced (URLs keep their
  host and lose only the credentials); `--flag value` pairs in argv are handled; the
  `last-applied-configuration` annotation and `managedFields` are dropped; `Secret` objects
  have all data replaced if one is ever passed in. ConfigMap values are never collected,
  only key names.
- Text rules: Authorization headers, bearer tokens, JWTs, URL credentials, `key=value`
  pairs with sensitive key names (connection strings), private key blocks, and well-known
  token prefixes (AWS, GitHub, Anthropic, Slack, Google).
- `build_user_prompt` only accepts the `RedactedEvidence` type and runs a final scan for
  the high-confidence patterns. If anything matches, it raises and nothing is sent.

Secret *names* and references are kept on purpose; "secret X not found" is a diagnosis.

## Running it

### Without a cluster or an API key

```bash
docker compose up --build
# UI on http://localhost:5175, API on http://localhost:8105
```

The backend starts in fixture mode (`KUBE_MODE=fixture`), serving all 20 fault captures as
if they were namespaces in one cluster, and answers diagnoses with the keyword baseline
(`LLM_MODE=fake`). To use the real model against the fixtures:

```bash
LLM_MODE=anthropic ANTHROPIC_API_KEY=sk-ant-... docker compose up --build
```

### Without Docker

```bash
cd backend
py -3.12 -m venv .venv                      # python3.12 -m venv .venv on Linux/macOS
.venv/Scripts/pip install -r requirements-dev.txt
cp .env.example .env                        # fixture mode, fake model; remove DATABASE_URL to use SQLite
.venv/Scripts/alembic upgrade head
.venv/Scripts/uvicorn app.main:app_factory --factory --port 8105

cd ../frontend
npm ci
npm run dev                                 # http://localhost:5175, proxies /api and /ws to :8105
```

With `KUBE_MODE=kubeconfig` the backend uses your current kubectl context instead.

### On a local kind cluster

```bash
deploy/kind/up.sh                           # needs docker, kind, kubectl, helm
kubectl -n kube-explorer port-forward svc/kube-explorer-frontend 5175:80

faults/run.sh list
faults/run.sh inject oom-killed             # watch it show up in the UI, then "Explain this failure"
faults/run.sh revert oom-killed             # applies the fix
faults/run.sh clean oom-killed              # deletes the namespace
```

`up.sh` uses the real model when `ANTHROPIC_API_KEY` is set in the environment and the
keyword baseline otherwise.

### On AWS

```bash
cd deploy/terraform
terraform init -backend-config=bucket=<state-bucket> -backend-config=key=kube-explorer.tfstate \
  -backend-config=region=<region>
terraform apply
aws secretsmanager put-secret-value --secret-id kube-explorer/prod \
  --secret-string '{"anthropic_api_key":"...","database_url":"postgresql+psycopg://..."}'

helm upgrade --install kube-explorer deploy/helm/kube-explorer -n kube-explorer --create-namespace \
  --set image.backend.repository=<backend repository_url> --set image.backend.tag=<tag> \
  --set image.frontend.repository=<frontend repository_url> --set image.frontend.tag=<tag> \
  --set 'networkPolicy.kubeApiServer.cidrs={<api server cidr>}' \
  --set 'networkPolicy.database.cidrs={<database subnet cidr>}'
```

The chart expects an existing PostgreSQL database, the External Secrets Operator, and a
`ClusterSecretStore` named `aws-secrets-manager` (configurable). Terraform outputs the IAM
policy to attach to the operator's role.

## Testing

```bash
cd backend
.venv/Scripts/ruff check . && .venv/Scripts/ruff format --check .
.venv/Scripts/pytest

cd ../frontend
npm run lint && npm run typecheck && npm test && npm run build
```

The backend tests need no external services: SQLite for the database, `FakeCluster` for
Kubernetes, a scripted model for Claude. What they cover:

| File | Subject |
| --- | --- |
| `test_redaction.py` | A fixture workload leaking credentials (18 canary strings) in env, args, annotations, events and logs; none may reach the prompt, and the hostnames, secret names and error messages must survive |
| `test_evidence.py` | Evidence collection for every scenario in the fault library, plus per-fault assertions |
| `test_watcher.py` | Resume from last `resourceVersion`, bookmarks, dropped connections, 410 as exception and as in-band event, list failures, backoff |
| `test_fanout.py` | View diffing on relist, change suppression, hub ordering, slow-consumer drop, snapshot/stream boundary |
| `test_schema.py` | Valid, fenced, truncated, wrong-typed and out-of-range model output; ranking; read-only command check |
| `test_real_reader.py` | `KubernetesReader` against a local HTTP stub of the API server |
| `test_llm.py` | Request sent to the Anthropic SDK and mapping of refusals, truncation and API errors |
| `test_api.py` | REST endpoints and the WebSocket end to end through real watcher threads |
| `test_eval.py`, `test_migrations.py`, `test_rbac.py`, `test_status.py` | Harness scoring and storage, Alembic vs models, ClusterRole vs code, pod status |

## Fault library and evaluation

Each directory under `faults/` holds:

- `inject.yaml`: a namespace `fault-<id>` with the broken resources
- `revert.sh`: the fix, as the `kubectl` command(s) you would run
- `scenario.yaml`: the diagnosis target, the expected root cause category, and the strings
  the collected evidence must contain
- `captured/`: cluster state (`cluster.yaml`) and container logs for the eval harness

| Scenario | Expected root cause |
| --- | --- |
| `service-selector-mismatch` | `service_selector_mismatch` |
| `wrong-container-port` | `service_port_mismatch` |
| `oom-killed` | `oom_killed` |
| `readiness-probe-failing` | `readiness_probe_failing` |
| `liveness-probe-failing` | `liveness_probe_failing` |
| `missing-configmap-key` | `missing_config_key` |
| `missing-configmap` | `missing_configmap` |
| `missing-secret` | `missing_secret` |
| `netpol-blocks-dns` | `network_policy_blocking` |
| `pvc-pending` | `pvc_unbound` |
| `bad-image-tag` | `image_pull_failure` |
| `private-registry-no-pull-secret` | `image_pull_auth` |
| `crashloop-bad-command` | `bad_command` |
| `quota-exceeded` | `resource_quota_exceeded` |
| `unschedulable-resources` | `insufficient_resources` |
| `node-selector-mismatch` | `node_selector_mismatch` |
| `untolerated-taint` | `untolerated_taint` |
| `init-container-failing` | `init_container_failure` |
| `rbac-forbidden` | `rbac_forbidden` |
| `read-only-rootfs` | `filesystem_permission` |

The harness loads each capture into a `FakeCluster`, runs the same collect, redact, prompt
and parse path as the API, and counts a scenario as passed when the **top** hypothesis has
the expected category. Runs and per-scenario results are stored in `eval_runs` and
`eval_results` and are readable at `/api/eval/runs`.

```bash
cd backend
export DATABASE_URL=postgresql+psycopg://kube_explorer:kube_explorer@localhost:5432/kube_explorer
alembic upgrade head

python -m app.eval --fake                         # keyword baseline, no API key
ANTHROPIC_API_KEY=sk-ant-... python -m app.eval    # the real model
python -m app.eval --scenario oom-killed --scenario pvc-pending
```

No accuracy figure is quoted here because none has been measured for the real model: this
repository was built without an API key. The command above prints the score and stores
it. `--fake` runs a deterministic keyword matcher (`baseline.py`); its score is a floor
that shows how much of the library plain string matching solves, not a model result.

### Where the captures come from

The `captured/` directories in this repository are **synthesized**, not recorded.
`faults/tools/synthesize_captures.py` takes each scenario's `inject.yaml` and adds the pods,
statuses, events and logs that the fault is known to produce on a three-node kind cluster.
The inject manifests and revert scripts have not been run against a cluster in this
repository's history either. To replace a capture with a real recording:

```bash
faults/run.sh inject pvc-pending
sleep 180                                    # let restarts and back-off settle
faults/capture.sh pvc-pending                # overwrites faults/pvc-pending/captured
```

Until that is done, eval results describe how the diagnoser handles plausible evidence, and
the wording of real kubelet and scheduler messages may differ in detail.

## Decisions and trade-offs

- **Official sync client in threads, not an async client.** The brief was the official
  client. Threads plus `call_soon_threadsafe` keep all shared state on the event loop, which
  is easier to reason about than locks.
- **Reading the watch stream directly instead of `watch.Watch()`.** The helper deserializes
  into typed models and retries a 410 internally. Raw JSON objects match the fixtures, and
  owning the loop makes `resourceVersion` handling explicit and testable.
- **Snapshot on connect instead of resumable browser streams.** The state is small, so
  resending it is cheaper than keeping a per-client event log.
- **Summaries, not full objects, over the WebSocket.** Smaller messages, and most
  `MODIFIED` events change nothing visible so they are not sent at all.
- **Closed category set for scoring.** Exact match on an enum is deterministic and cheap.
  The cost is that a correct diagnosis filed under a neighbouring category counts as a miss.
- **Pattern-based redaction.** There is no list of known secret values to match against
  because the app cannot read Secrets. Patterns can miss an unusual format; the mitigations
  are no Secret access, structural rules, and the fail-closed final check.
- **One process holds the live state.** Each replica runs its own watches. Replicas are
  independent and consistent with the API server, at the cost of N sets of watches.
- **No authentication in the app.** It is meant to sit behind an ingress that does it.

## Known limitations

- Fault captures are synthesized and the fault scripts are untested against a live cluster
  (see above). The Docker images, compose file, kind scripts, GitHub Actions workflow and
  the real Anthropic call have also not been executed; they were written without Docker, a
  cluster or an API key available.
- Logs in the UI are polled every five seconds over REST, not streamed over the WebSocket.
- Jobs, CronJobs and bare Services cannot be chosen as diagnosis targets.
- The ClusterRole can read ConfigMap values even though the collector only uses key names.
- Plain NetworkPolicy has no FQDN rules, so backend egress on 443 is open to all public
  addresses, not just `api.anthropic.com`.
- Evidence is sent to the model in one request with no token budget check; a namespace with
  very many Services or NetworkPolicies makes a large prompt.
- Diagnoses are synchronous HTTP requests and can take a while at high effort.

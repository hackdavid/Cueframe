# Usage, plans and central logs

## Plans and limits
Limits live in `app/plans.py` (`LIMITS`) and count per calendar month (UTC):

| Plan | total (lessons + videos) | lesson | video (tutorial/reel/explainer) | edit |
|---|---|---|---|---|
| free | 3 | – | 1 | 3 |
| student | – | 150 | 1 | 20 |
| creator | 30 | – | – | 100 |
| studio | 120 | – | – | 400 |
| unlimited | – | – | – | – |

"–" means no limit. You can override them without a deploy using
`CUEFRAME_PLAN_LIMITS='{"free": {"total": 5}, "team": {"total": 300}}'`. That also adds new plans.
- New accounts get `CUEFRAME_DEFAULT_PLAN` (default `free`).
- The admin account gets `CUEFRAME_ADMIN_PLAN` (default `unlimited`).
- An unknown plan gets the Free limits.

## How a video is metered
1. **Reserve:** the job handler (`board.make`, `tutorial.make`, `tutorial.edit`, or the chat
   router before it asks for a format) calls `usage.gate`. This atomically reserves one
   `usage_records` row, before any planning or sandbox work starts.
2. **Over the limit:** the chat explains which limit was hit, when it resets and where to
   upgrade. No sandbox is started.
3. **Finish:** the job manager closes the record as `succeeded`, `failed` or `cancelled`.
   A server restart closes it as `interrupted`. Failed and cancelled ones are refunded,
   i.e. not counted.
4. **Tagged:** each record carries `user_id`, `session_id` (the login), `conversation_id`,
   `job_id`, category, kind and plan.

## Central logs
- Every `cueframe.*` log line at INFO or above is stored in the `logs` table, tagged with the
  current user, session, conversation and job. Lines also go to stdout in the same format.
- Mutating API calls and failed calls are logged too: method, path, status and ms.
- Logs are kept for `CUEFRAME_LOG_RETENTION_DAYS` (30) days.

## APIs and the admin page
- `GET /api/usage`: your plan, limits, used, remaining, reset date and recent records.
- Admin only:
  - `GET /api/admin/usage`: filters `user_id`, `session_id`, `conversation_id`,
    `job_id`, `category`, `kind`, `status`, `plan`, `since`, `until`. Returns records and
    totals.
  - `GET /api/admin/logs`: the same id filters, plus `level` (minimum), `logger` (prefix),
    `q` (text) and `before` (id, for paging).
  - `GET`, `POST /api/admin/users` and `PATCH /api/admin/users/{id}`: list accounts,
    create password accounts, and change plan or role.
- `/admin`: tabs for usage, logs and users. Click any id to filter by it.

## Adding Firebase Google sign-in later
Accounts already support external identities: `users.auth_provider` and `provider_uid`,
with a unique index on the pair. Steps:
1. Add the Firebase web SDK to `/login` and sign in with Google to get an ID token.
2. Add `POST /api/auth/firebase {id_token}`. Verify the token with
   `firebase_admin.auth.verify_id_token`, then call
   `auth.login_identity(store, "google", decoded["uid"], email=..., name=..., plan=settings.default_plan)`.
3. Set the returned token as the `cf_session` cookie, exactly as `/api/auth/login` does.

Usage, limits and logs work unchanged, because they key on `users.id` and the session id.
Stripe webhooks later only need to call `store.update_user(id, plan=...)`.

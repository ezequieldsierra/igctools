# ChatGPT connector — scripts, printing and System Console

This module connects ChatGPT to scripts, print formats and System Console on the
configured Frappe site. It runs in Frappe's web workers and uses the dependencies already
provided by Frappe 15. It does not require `frappe-mcp`, a second Frappe app, a
Desktop Commander agent, or an open terminal for normal operation.

## Deployment and configuration

1. Deploy the app revision and run the normal Frappe migration. The migration
   adds **IGC MCP Settings**, **IGC MCP Change**, **IGC MCP Grant**, and
   **IGC MCP Console Run**. The connector
   defaults to disabled.
2. As a System Manager, open **IGC MCP Settings** through the Desk search bar.
3. Select **Allowed User**, set **Site URL** to the public HTTPS origin of the
   site, check **Enabled**, and save. The selected account must be an enabled
   System User with System Manager and Script Manager roles, or Administrator.
4. Copy **Server URL** and the public **OAuth Client ID** displayed after saving.
   The settings document creates a dedicated OAuth Client using S256 PKCE and
   `Token Endpoint Auth Method: None`. Do not copy a client secret.
5. In ChatGPT, open **Plugins → + → New Plugin**. Enter a name, paste **Server
   URL**, choose **OAuth**, and supply the public client ID in **Advanced OAuth
   settings**. Leave the client secret empty. Complete the Frappe sign-in and
   click **Allow** on the consent page.
6. Start a chat with this connector. Call `connection_info`, then search and
   read a known script. Confirm the site and user before requesting an edit.

The OAuth issuer advertises issuer identification (`iss`) and uses the callback
`https://chatgpt.com/connector_platform_oauth_redirect`. If the ChatGPT management
page specifies a different callback for the account, check that before connecting;
do not introduce wildcard callback URLs.

## Behavior

- Version 2.0.0 adds eight print tools and two console tools to the original seven
  script tools. Existing script edits remain limited to the `script` field;
  activation, document triggers, target DocTypes and API names are preserved.
- Print tools inspect document fields and available formats, read complete format
  JSON in character chunks, create/update custom Jinja formats, render previews,
  list backups, restore earlier source, and change a DocType's default.
  Format updates require the saved revision. Default changes require both the
  format revision and the current default revision. Native Frappe permissions and
  validation run; existing standard, builder and raw-print formats are preserved.
  Print-format creation, edits and defaults have exact before/after snapshots in
  IGC MCP Change. Creation snapshots have no previous format to restore.
- Console jobs are explicitly requested through `execute_system_console`. They use
  native Frappe SafeExec for Python and native read_sql for SQL, require the
  configured System Manager and System Console write permission, and recheck
  authorization when the worker starts. Server Scripts must already be enabled;
  this connector does not change that configuration. No unrestricted Python exec
  endpoint is added.
- Reuse the same request_id on retries. A changed payload with an existing id is
  rejected, and completed/running execution rows are never executed again.
  Jobs run in the existing short queue with a 180-second worker timeout.
  `read_console_output` returns status and paginated output, traceback or source.
  Only the configured originating user can read a run through the connector.
- Commit defaults to false. Direct commit/rollback calls inside Python are disabled.
  Successful requested database changes and their completion audit are committed
  together; failures and Commit=false roll back script changes. Execution records
  and native Console Log entries persist even for rollback runs. Database rollback
  cannot reverse files, emails or network effects.
- Output is bounded at one million characters and explicitly marked when truncated.
  Python prints before an exception are retained separately from its traceback.
  A terminated worker can leave a run marked Running: inspect the queue before
  taking any action, and never submit the same operation with a fresh request id.
  Console audits are not backups of every business record the script may modify;
  scripts that modify existing data should explicitly create the needed backups.
- Each write requires the revision hash returned by `read_script`. The document
  is locked while the revision is checked and saved. An edit made by someone
  else causes the operation to fail instead of overwriting it.
- Backups contain complete document snapshots and SHA-256 hashes. They are
  non-executable IGC MCP Change records with no edit or delete permissions.
  Restoring source creates a new backup of the source being replaced.
- Source, backup and verification share one transaction. Any failure rolls back
  the operation. Frappe's normal save hooks and permission checks still run.
- Server Script source is compiled with RestrictedPython but never explicitly
  executed by a tool. Client Script source is saved and compared byte for byte.
  Successful saving is **not** a JavaScript syntax check or a functional test.
  Active scripts will subsequently execute through their normal Frappe triggers.
- Only the configured user can use the connector. OAuth tokens are bound to the
  configured client, resource and user, with expiration and revocation checks.
  The Frappe user and native OAuth token retain their native Frappe permissions;
  the connector's restricted tool list is not a site-wide OAuth scope sandbox.
- Tokens are opaque and stored by Frappe's OAuth implementation. IGC MCP Grant
  stores their hashes, resource binding and expiration, not plaintext secrets.
  Refresh tokens rotate and revoke the previous token. Grants must be retained
  while in use; database backups should include the connector DocTypes.

## Disable or revert

Uncheck **Enabled** in IGC MCP Settings to block the connector. Existing scripts
keep working. Use `script_history` and `restore_script` to undo a source change;
the current revision is still required. App rollback and script-source rollback
are separate operations because scripts live in the site database.

## Validation

After deploying version 2, run the normal site migration, then refresh the plugin
tool definitions in ChatGPT. The OAuth URL and client ID remain unchanged.

Run integration tests with the bench environment after migrating the new DocTypes:

```bash
bench --site YOUR_DEVELOPMENT_SITE execute igctools.test_mcp_extended.run
```

Tests use a temporary disabled Client Script and temporary OAuth documents, and
roll back all test data. They cover backup integrity, stale revisions, atomic
rollback, permissions, protocol responses, PKCE, native Frappe authentication,
token binding, refresh and revocation. Run these tests on a development/test site.
Some stripped development sites lack Energy Point Settings; in that case the
runner disables that unrelated hook only inside the test process. The additional
console transaction tests create and commit uniquely named temporary ToDo/run/log
records, verify rollback and Commit behavior, and remove those fixtures afterward.
The print tests roll back their temporary formats and default changes.

The transport implements the stateless JSON response form of MCP Streamable
HTTP, protocols 2025-03-26 and 2025-06-18. GET streaming is explicitly unsupported
(405); POST notifications return 202. No server-side session process is needed.

References: [MCP transport](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports)
and [ChatGPT OAuth](https://developers.openai.com/plugins/build/auth).


## Continuous integration and reviewed privileged operations

CI creates an isolated Frappe 15.120.1 site on Ubuntu 22.04 and runs the app tests,
the 39 MCP integration/transaction tests and three SVG/PDF/DXF regression tests.
Server Scripts are enabled only on that disposable CI site so console tests execute.
The developer and production sites are not configured by this workflow.

CairoSVG 2.9.1 and lxml 6.1.3 replace the vulnerable pinned export dependencies.
The export tests check physical dimensions, curves, stroke colors, DXF units and
SVG deduplication. The Python and JavaScript formatting checks cover maintained
source; the vendored, compiled face-api bundle is excluded from source formatting.
Dependency audit and Semgrep remain enabled.

Semgrep review dispositions are limited to specific call sites and rules:

- Guest HTTP routing is needed for the OAuth challenge, login redirect and token
  exchange. Every MCP operation requires a resource-bound bearer token and the
  configured user. PKCE, replay, expiry/resource and Guest-denial tests cover this.
- System Console intentionally executes restricted Python. It checks the configured
  user, System Manager and native console permission before queueing, in the worker,
  and immediately before execution. Native SafeExec restrictions remain active;
  script-level commit/rollback is blocked. The queue actor is an immutable audit field.
- Worker commits preserve status and enforce explicit Commit semantics; transaction
  tests cover rollback, failure, revocation, deduplication and retained output.
- Existing file export commits preserve the established persisted-download contract;
  the project rebuild checkpoints completed batches. Test commits affect test records.
- Certificate reads use the installed CA store and a fixed bundled certificate path.
  No API caller controls these paths. The temporary bundle is flushed and closed before use.
- Consent rendering uses a fixed application template with escaped context.
- The existing Job Card subclass uses the v15 override hook; extend_doctype_class
  is available only in v16+, outside this app's supported framework range.

These dispositions do not disable rules globally or change their severity. Each
remaining intentional operation has an adjacent explanation and a rule-specific
annotation; new occurrences remain subject to scanning.

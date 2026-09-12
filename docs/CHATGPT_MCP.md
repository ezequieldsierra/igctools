# ChatGPT script connector

This module connects ChatGPT to existing Client Scripts and Server Scripts in the
site's database. It runs in Frappe's web workers and uses the dependencies already
provided by Frappe 15. It does not require `frappe-mcp`, a second Frappe app, a
Desktop Commander agent, or an open terminal for normal operation.

## Deployment and configuration

1. Deploy the app revision and run the normal Frappe migration. The migration
   adds **IGC MCP Settings**, **IGC MCP Change**, and **IGC MCP Grant**. The connector
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

- Seven tools: connection information, name search, paginated source reading,
  full source replacement, unique text replacement, change history, and restore.
- Writes are limited to the `script` field on existing Client Script and Server
  Script documents. Activation, document triggers, target DocTypes and API names
  are preserved. There is no create, delete, SQL or arbitrary execution tool.
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
  while in use; database backups should include the three new DocTypes.

## Disable or revert

Uncheck **Enabled** in IGC MCP Settings to block the connector. Existing scripts
keep working. Use `script_history` and `restore_script` to undo a source change;
the current revision is still required. App rollback and script-source rollback
are separate operations because scripts live in the site database.

## Validation

Run integration tests with the bench environment after migrating the new DocTypes:

```bash
bench --site YOUR_SITE execute igctools.test_mcp.run
```

Tests use a temporary disabled Client Script and temporary OAuth documents, and
roll back all test data. They cover backup integrity, stale revisions, atomic
rollback, permissions, protocol responses, PKCE, native Frappe authentication,
token binding, refresh and revocation. Run these tests on a development/test site.
Some stripped development sites lack Energy Point Settings; in that case the
runner disables that unrelated hook only inside the test process and reports it.

The transport implements the stateless JSON response form of MCP Streamable
HTTP, protocols 2025-03-26 and 2025-06-18. GET streaming is explicitly unsupported
(405); POST notifications return 202. No server-side session process is needed.

References: [MCP transport](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports)
and [ChatGPT OAuth](https://developers.openai.com/plugins/build/auth).

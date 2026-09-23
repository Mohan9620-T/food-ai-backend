# Configure Food AI account emails

Food AI sends welcome emails after registration. One authorized sender can send to
Gmail, Google Workspace, Outlook, Hotmail, Microsoft 365, and other recipient domains.
Recipients do not supply their email passwords to Food AI. A Food AI login password
is separate from the sender's mail-provider credentials and is never emailed.

## New-account sign-in and password emails

Configure `PUBLIC_APP_URL` with the deployed UI's HTTPS URL, for example
`https://food-ai-ui-production.up.railway.app`. New-account emails include the
recipient's sign-in address, login URL, and a private link to choose a new password.
The user can also log in immediately with the password chosen during registration.

The password link expires after 30 minutes and works once. Only its hash is saved
in the database; the private token is never returned by registration. Using it
invalidates existing access and refresh tokens and sends a password-change notice.
The browser removes the token from its address bar and keeps it only in memory.
Do not refresh the form before saving; reopen the email link if necessary.
Missing/invalid `PUBLIC_APP_URL` disables the link without preventing registration.
Mail delivery failure likewise does not prevent login. Existing accounts are not
automatically sent new emails, and their passwords cannot be retrieved or emailed.

This follows [OWASP's password-link guidance](https://cheatsheetseries.owasp.org/cheatsheets/Forgot_Password_Cheat_Sheet.html).

**Railway Free, Trial and Hobby block outbound SMTP.** Use one of the HTTPS providers
below on these plans. [Railway's email delivery rules](https://docs.railway.com/networking/outbound-networking).
SMTP is supported locally and on Railway Pro or above. No plan upgrade is required
when using HTTPS.

Set secrets in **Railway → food-ai-backend → Variables**, then redeploy. Do not put
real credentials in these examples, Git, browser code, screenshots or chat messages.
Every secret setting also accepts a `_FILE` environment variable pointing to a mounted
secret file. `EMAIL_FROM_EMAIL` is a bare address, without a display name.

## Choose one provider

| Sender | EMAIL_PROVIDER | Authentication |
| --- | --- | --- |
| Verified custom-domain sender through Resend | `resend` | Resend API key |
| Gmail or Google Workspace mailbox | `gmail` | Gmail API OAuth consent |
| Personal Outlook/Hotmail mailbox | `microsoft` | Delegated Microsoft Graph OAuth consent |
| Microsoft 365 mailbox | `microsoft` | Delegated OAuth or tenant application credentials |
| Existing SMTP sender, on an SMTP-enabled host | `smtp` | SMTP app password / permitted provider credentials |

`EMAIL_PROVIDER=auto` preserves old SMTP configurations and disables sending when
SMTP_HOST is absent. `disabled` explicitly disables sending. There is no automatic
fallback to another sender or transport after an error, avoiding duplicate messages.

## Resend over HTTPS

Verify a domain you control in Resend and complete its DNS requirements. A free
Gmail/Outlook address is not a custom domain you can verify there. Create a sending
API key for that domain. The sandbox sender only supports restricted test recipients.

```text
EMAIL_PROVIDER=resend
EMAIL_FROM_EMAIL=notifications@your-verified-domain.example
EMAIL_FROM_NAME=Food AI Assistant
RESEND_API_KEY=<sending API key>
```

[Resend send API](https://resend.com/docs/api-reference/emails/send-email).

## Gmail / Google Workspace over HTTPS

### Guided local connection (recommended)

1. Open [Google Cloud Console](https://console.cloud.google.com/apis/library/gmail.googleapis.com)
   under the sender's account, select/create a project and enable **Gmail API**.
2. In **Google Auth Platform**, configure the app name/contact and audience. While
   the app is in Testing, add the sender address as a test user.
3. Create a **Desktop app** OAuth client and download its JSON into the ignored
   `secrets/google-oauth-client.json` directory/file in this backend repository.
   Do not paste the JSON, Google password or OAuth tokens into chat or Git.
4. Run the following locally, replacing the sender and project values:

```powershell
.\.venv\Scripts\python.exe scripts/connect_gmail.py --client-json secrets/google-oauth-client.json --sender your-sender@gmail.com --project YOUR_RAILWAY_PROJECT_ID
```

The helper opens Google consent in your browser. It requests email identity to
verify the sender and Gmail sending permission, with offline access. A loopback
callback uses state validation and PKCE. Credentials go directly to Railway through
stdin without being printed or saved to another file. Existing encryption keys are
preserved. Redeploy the backend afterward; the helper does not send any messages.

External apps in Testing normally receive seven-day refresh tokens for these
scopes. Complete Google's applicable publishing/verification requirements for
continued production delivery. Keep the client JSON private.

[Google desktop OAuth](https://developers.google.com/identity/protocols/oauth2/native-app).

### Manual configuration

1. In your own Google Cloud project, enable Gmail API and create an OAuth client.
2. Configure the consent screen and authorize the intended sender with only
   `https://www.googleapis.com/auth/gmail.send`, requesting offline access.
3. Exchange the authorization code for a refresh token using that same client.
   Google's OAuth Playground can use your own client credentials for setup; configure
   its documented redirect URL if you use it. Do not use unrelated/shared client tokens.
4. Set the following in Railway. The From address must be the authorized account
   or one of its verified send-as aliases.

```text
EMAIL_PROVIDER=gmail
EMAIL_FROM_EMAIL=your-sender@gmail.com
GMAIL_CLIENT_ID=<your Google OAuth client ID>
GMAIL_CLIENT_SECRET=<your OAuth client secret>
GMAIL_REFRESH_TOKEN=<refresh token from sender consent>
EMAIL_TOKEN_ENCRYPTION_KEY=<generated Fernet key>
```

A regular Google account password or API key alone cannot authorize Gmail API mail.
External OAuth apps left in Testing can issue refresh tokens that expire after
seven days; finish the applicable production/verification setup for durable use.

[Google OAuth offline access](https://developers.google.com/identity/protocols/oauth2/web-server#offline),
[Gmail send API](https://developers.google.com/workspace/gmail/api/guides/sending).

## Microsoft personal Outlook / Hotmail, or delegated Microsoft 365

1. Register your application in Microsoft Entra. For personal Outlook/Hotmail,
   include personal Microsoft accounts in the supported account types.
2. Use authorization-code OAuth with delegated `Mail.Send` and `offline_access`.
   Sign in as the intended sender and grant consent. The account or tenant may
   require administrator approval.
3. Set the client ID and refresh token. A confidential web application also needs
   its client secret; a correctly registered public client does not.

```text
EMAIL_PROVIDER=microsoft
EMAIL_FROM_EMAIL=your-sender@outlook.com
MICROSOFT_TENANT_ID=common
MICROSOFT_CLIENT_ID=<registered application ID>
MICROSOFT_CLIENT_SECRET=<web-app secret; blank for a public client>
MICROSOFT_REFRESH_TOKEN=<sender's delegated refresh token>
EMAIL_TOKEN_ENCRYPTION_KEY=<generated Fernet key>
```

`consumers` can be used for personal accounts only, or a specific tenant ID for
Microsoft 365. The delegated token must belong to the configured sender.

## Microsoft 365 application credentials

For a server-owned work mailbox, register a tenant application, configure Graph
application `Mail.Send`, obtain admin consent, and restrict its Exchange mailbox
access to the intended sender. The tenant administrator must configure that scope.
Personal Outlook accounts do **not** support this client-credentials mode.

```text
EMAIL_PROVIDER=microsoft
EMAIL_FROM_EMAIL=notifications@your-company.example
MICROSOFT_TENANT_ID=<specific tenant ID>
MICROSOFT_CLIENT_ID=<application ID>
MICROSOFT_CLIENT_SECRET=<application secret value>
MICROSOFT_REFRESH_TOKEN=
```

Remove any previous delegated refresh token when switching to application mode.
[Microsoft sendMail permissions](https://learn.microsoft.com/en-us/graph/api/user-sendmail?view=graph-rest-1.0),
[authorization-code flow](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow),
[application credentials](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-client-creds-grant-flow).

## OAuth refresh token storage

Generate a Fernet key **in your own terminal**, and paste it only into the Railway
secret field. For example:

```powershell
.\.venv\Scripts\python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Keep this key stable and backed up privately. Migration `0014_mail_credentials`
creates a table for encrypted rotated tokens. Access tokens are used only in memory.
The cache is scoped to the provider, tenant, client ID and original refresh token;
replacing the account token selects a separate configuration. Rotated refresh tokens
survive process restarts and deployments. Revocation, tenant policy or expiry can
still require renewed account consent. Changing the encryption key requires a fresh
authorized refresh token; old ciphertext cannot be read with a different key.

## SMTP / app password

For Gmail SMTP on an allowed network, enable two-step verification and create an
app password where the account permits it. Do not use the normal account password.

```text
EMAIL_PROVIDER=smtp
EMAIL_FROM_EMAIL=your-sender@gmail.com
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your-sender@gmail.com
SMTP_PASSWORD=<Google app password>
SMTP_USE_TLS=true
SMTP_USE_SSL=false
```

For implicit TLS use port 465 and `SMTP_USE_SSL=true`. SSL takes precedence over
STARTTLS. Authenticated SMTP without TLS is rejected. Prefer Microsoft Graph for
Outlook/Microsoft 365 instead of legacy password-based SMTP authentication.
[Google SMTP/app-password configuration](https://support.google.com/a/answer/176600?hl=en).

## Check configuration and delivery

These commands use the environment of the machine/container where they run.

```powershell
# Local check: prints provider and missing setting names, never secret values.
.\.venv\Scripts\python.exe -m app.email_setup --check

# Railway container check: no email is sent.
railway ssh --service food-ai-backend -- python -m app.email_setup --check

# Explicitly send one test to an address you control (replace the address).
railway ssh --service food-ai-backend -- python -m app.email_setup --test-to your-address@example.com
```

A successful API/SMTP response means **accepted by the provider**, not confirmed
inbox delivery. Check the receiving inbox/spam folder and provider delivery logs.
The existing registration `email_sent` field reports this provider acceptance.
Email failure does not undo registration or reveal provider secrets to the user.
No automatic send retry occurs after an ambiguous timeout.

This setup sends application emails. It does not add Google/Microsoft login,
read inboxes, or implement a forgotten-password/reset flow.

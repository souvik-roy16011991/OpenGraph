# 02 — Accounts & sign-in

Everything you need to know about creating an account, signing in, staying signed in, and signing out.

## Creating an account

### Option A — Email & password

1. Go to `/sign-up`.
2. Enter:
   - **Email** — will be the identifier for your account. Must be real; we send nothing to it today, but it becomes your tenant boundary.
   - **Display name** *(optional)* — how you'll appear in the UI header and audit logs.
   - **Password** — minimum 8 characters. Stored bcrypt-hashed.
   - **Confirm password** — must match.
3. Click **Create account**.

You're signed in immediately. You land on `/` (home) with an active session and no workspace yet.

### Option B — Sign up with GitHub

1. Go to `/sign-up` (or `/sign-in`, both pages have the button).
2. Click **Continue with GitHub**.
3. GitHub shows its consent screen. The app requests two scopes:
   - `read:user` — your public profile
   - `user:email` — your verified email addresses
4. Click **Authorize**. GitHub redirects you back to the app.
5. You land briefly on `/auth/complete` while the app hydrates your session, then get bounced to `/` (or wherever you were trying to go before you were asked to sign in).

**If you see "Your primary GitHub email isn't verified":** your GitHub primary email needs to be verified in GitHub's settings. Go to https://github.com/settings/emails, verify it, come back, and retry. See [19 — Troubleshooting](19-troubleshooting.md) for the full list of OAuth error codes.

### What happens if my GitHub email matches an existing password account?

They auto-link. If you signed up yesterday with `alice@example.com` + password, and today you click "Continue with GitHub" on a GitHub account whose verified primary email is also `alice@example.com`, the same user row now has both identities. Your password still works; you've just added a second way to get in.

## Signing in

### With password

1. Go to `/sign-in`.
2. Enter email + password.
3. Click **Sign in**.

The sign-in form doesn't have a "Forgot password" flow today — if you lose your password, contact support or re-register via GitHub on the same verified email.

### With GitHub

1. Go to `/sign-in`.
2. Click **Continue with GitHub**.
3. If you've already approved the app on GitHub, you won't see the consent screen again — you're just bounced through.

### Deep links (`?return_to=…`)

If you click a link that requires sign-in (e.g. `/workspaces`) while signed out, the middleware redirects you to `/sign-in?return_to=/workspaces`. After signing in, you land back on `/workspaces` instead of the default home page. This works for both password and GitHub flows.

## Staying signed in

- **Session length:** 30 days.
- **Where it's stored:** a JWT in `localStorage` (so the app can attach it as `Authorization: Bearer …` on API calls) and mirrored in a non-HttpOnly cookie named `auth_token` (so the Next.js middleware can see you're logged in and bypass the sign-in redirect).
- **When it ends:**
  - You click **Sign out**.
  - The server's `JWT_SECRET` gets rotated (operator action — invalidates every session at once).
  - 30 days pass.
  - You clear site data in your browser.

## Signing out

1. Click your avatar in the top-right.
2. Click **Sign out** in the dropdown.

Or, on `/profile`, click **Sign out of this device**.

Signing out:
- Clears `localStorage` and the `auth_token` cookie.
- Invalidates the in-memory React Query cache so nothing leaks between accounts on a shared machine.
- Sends you to `/sign-in`.

## The profile page

`/profile` shows:

- Your avatar (generated from your initials), email, display name, and plan tier.
- Quick stats: workspaces, builds, chat turns.
- A link to `/billing` with your current credit balance.
- **Edit** (pencil) next to your display name — inline edit, save or cancel.
- **Sign out of this device** button.
- An **audit trail** — every action you've taken (sign-up, workspace.create, file.upload, chat.query, etc.) with timestamps. Filterable by action. See [16 — Profile & audit](16-profile-and-audit.md).

## Deleting your account

There is no self-serve "Delete my account" button in the UI today. To delete:

- **If you're on free trial:** stop using the app. The tenant is dormant. Ask support if you want hard-delete.
- **If you're on a paid plan:** email support; they can cancel the subscription and issue a data deletion.

Operators can run `scripts/migrate_anon_workspaces.py --delete-all <user_id>` — that's the admin-side escape hatch.

## What determines "who I am" to the backend?

Two things, together:

1. **`Authorization: Bearer <jwt>`** — the JWT signed by the server. The `sub` claim is your user UUID.
2. **`X-Workspace-Id: <uuid>`** — which workspace you're acting on. The backend checks this workspace's `user_id` matches the JWT's `sub`. If not, you get a 404 (the app deliberately hides the existence of other tenants' workspaces).

You never set these headers yourself — the frontend attaches them to every call.

## FAQ

**Q: Can I have two email accounts on one GitHub login?** No — GitHub has one primary verified email, and that's what we key on. To run two tenants, use two GitHub accounts or use password auth on two different emails.

**Q: Can I change my email?** Not in the UI today. The display name is editable on `/profile`.

**Q: My password keeps getting rejected.** The signup form enforces 8 characters minimum. Try pasting it somewhere visible first — copy-paste mistakes are common. If it's definitely right, the server is probably returning 401 because the account doesn't exist; try signing up instead.

**Q: Does the app email me?** No. The app does not send any email today — no verification, no password reset, no notifications. If you lose password access, the GitHub link is the recovery path.

**Q: Is GitHub OAuth required?** No. Email/password works fully standalone. GitHub OAuth is only available if the operator has set `GITHUB_CLIENT_ID` + `GITHUB_CLIENT_SECRET` in the server config. If not, the "Continue with GitHub" button is either hidden or returns a 503 when clicked.

**Q: Can I use SSO (Okta / Azure AD / Google Workspace)?** Not today. Enterprise-only feature on request — mention it on the billing *Book a call* link.

See also: [19 — Troubleshooting](19-troubleshooting.md) for every error code you might see on the sign-in page.

# VoteCentral System Architecture

## Architecture Summary

VoteCentral Phase 1 is a server-rendered Django application for paid competitions. The system is optimized for reliable payment-confirmed vote recording, organizer ownership boundaries, and fast iteration over the first commercial use case.

The architecture is deliberately narrower than the long-term product vision. Phase 1 focuses on:

- organizer authentication with Django allauth
- paid event and nominee management
- public event microsites
- Paystack payment processing
- vote accounting
- ledger-backed organizer revenue
- dashboard reporting

The following are deferred to later phases:

- polls and elections
- OTP or token verification
- WebSockets
- advanced fraud analysis
- multi-organization tenancy
- custom internal admin dashboard

## High-Level Stack

### Client Layer

- Django templates
- HTMX for partial refreshes
- Alpine.js for light client behavior
- Tailwind CSS for styling

### Application Layer

- Django 6
- Django allauth for session-based organizer authentication
- Django admin for platform operations

### Domain Services

- event management
- nominee management
- payment processing
- vote accounting
- wallet ledger posting
- withdrawal request and reconciliation workflows
- organizer dashboard reporting
- email notification delivery
- Hubtel SMS notification delivery

### Data Layer

- PostgreSQL as the intended application database
- SQLite fallback for local bootstrap
- local media storage in development
- S3-compatible storage in staging and production

### Deferred Infrastructure

- Redis-backed WebSockets
- advanced analytics pipelines

## Core Domain Model

### Accounts

- `User`
  custom auth model using email as the login identifier

### Events

- `Event`
  owned by an organizer user
  contains title, slug, description, banner, currency, vote price, start/end dates, status, visibility, and publish timestamp

### Nominees

- `Nominee`
  belongs to an event
  contains name, slug, vote code, bio, photo, display order, and active status

### Payments

- `PaymentAttempt`
  created before redirecting a voter to Paystack
  stores amount, currency, vote quantity, payer metadata, gateway reference, gateway response data, and status

### Votes

- `VotePurchase`
  created only after a successful confirmed payment
  stores the purchased vote quantity, amount paid, voter metadata, payment reference, and timestamp

### Wallets

- `WalletAccount`
  one account per organizer and one platform account
- `LedgerTransaction`
  immutable accounting group for a payment posting
- `LedgerEntry`
  signed entries that must sum to zero per transaction

## Authentication and Authorization

### Authentication

- session-based auth through Django allauth
- email-only login
- guest voting without an authenticated voter account

### Authorization

- organizer routes are ownership-scoped by `request.user`
- platform-wide visibility is handled through Django admin and `is_staff`
- public voters can only interact with published event pages

## Request Flows

### Organizer Event Publishing Flow

1. Organizer logs in
2. Organizer creates a draft event
3. Organizer adds active nominees
4. Organizer publishes the event
5. System validates nominees, vote price, and date window before changing state

### Public Vote Purchase Flow

1. Voter opens a published active event page
2. Voter selects a nominee and vote quantity
3. Application creates a `PaymentAttempt`
4. Voter is redirected to Paystack hosted checkout
5. Paystack sends a webhook after payment completion
6. Application validates the webhook signature
7. Application verifies the event can still accept votes
8. Application creates one `VotePurchase`
9. Application posts one balanced ledger transaction
10. Leaderboard and organizer dashboard reflect the purchase

## Leaderboard Strategy

Phase 1 does not use WebSockets. Leaderboards are computed from successful vote purchases using database aggregation and refreshed on the public event page through HTMX polling.

This keeps the first release simple while preserving a clean upgrade path to Redis-backed realtime updates in Phase 2.

## Payment Strategy

- Paystack is the first and only Phase 1 provider
- provider integration is isolated behind a service layer so Flutterwave can be added later
- webhook confirmation is the source of truth
- idempotency is enforced by unique payment references and transaction-safe processing

## Wallet and Ledger Strategy

- organizer balances are derived from immutable ledger entries
- a successful payment creates an organizer sale credit, a platform fee credit, and a balancing platform debit
- mutable summary fields are not the source of truth in Phase 1

## Security and Fraud Controls

Phase 1 includes:

- CSRF protection on user-initiated form posts
- webhook signature validation
- ownership checks on organizer routes
- event state validation before vote creation
- unique payment references
- cache-backed request throttling for payment initiation and webhook endpoints
- admin visibility into payments, vote purchases, and ledger postings
- organizer-controlled SMS opt-in and normalized phone storage

Phase 1 defers:

- device fingerprinting
- behavioral fraud scoring
- SMS verification
- voter allowlists and token gating

## Media and Storage

- local media files in development
- S3-compatible storage planned for non-local deployments
- event banners and nominee photos stored through Django file fields

## Deployment Notes

Phase 1 can run locally with eager Celery tasks, but non-local notification delivery now assumes a Django web process plus Celery worker. Redis remains optional for local correctness and becomes the intended broker in staged or production environments.

This keeps local development simple while making asynchronous email and SMS delivery production-ready.

# VoteCentral Product Requirements Document

## Product Summary

VoteCentral Phase 1 is a paid-competition platform for Ghana-focused voting events. The first release is intentionally narrow: organizers create paid competitions, add nominees, publish public event pages, collect votes through Paystack, and track revenue through a ledger-backed wallet.

Polls, secure elections, OTP/token verification, WebSockets, and advanced anti-fraud controls are explicitly deferred until later phases.

## Phase 1 Goals

- Allow an organizer to create and publish a paid competition in minutes
- Let guest voters pay for votes without creating an account
- Record votes only after verified payment confirmation
- Show near-real-time leaderboards through HTMX polling
- Give organizers clear vote and revenue reporting
- Keep platform operations manageable through Django admin

## User Roles

### Platform Admin

- manage organizers and events
- inspect payments, vote purchases, and ledger entries
- intervene on suspended or cancelled events

### Organizer

- sign up and log in
- create and update paid competitions
- add and manage nominees
- publish, unpublish, and close events
- view event performance, votes, and revenue

### Voter

- browse active public events
- select a nominee
- choose vote quantity
- pay through Paystack
- see leaderboard and event progress

## Phase 1 Product Scope

### Included

- organizer authentication with Django allauth
- paid competition event creation
- nominee CRUD
- public event microsites
- Paystack payment initiation and webhook confirmation
- vote recording after successful payment
- near-real-time leaderboard via HTMX polling
- organizer wallet ledger posting
- transactional email notifications
- Hubtel-backed SMS notifications for organizers and voter payment outcomes
- Django admin operations for platform staff

### Deferred

- public polls
- secure elections
- OTP or voter token verification
- WhatsApp notifications
- USSD and QR voting
- multi-round competitions
- WebSocket-based realtime updates
- external API access
- advanced fraud scoring and device fingerprinting

## Organizer Flow

1. Organizer signs up and logs in
2. Organizer creates a draft competition
3. Organizer adds nominees
4. Organizer publishes the event
5. Public voters visit the event microsite
6. Voter selects a nominee and vote quantity
7. Voter pays through Paystack
8. Vote purchase is confirmed by webhook
9. Leaderboard and organizer dashboard reflect the successful purchase

## Core Functional Requirements

### Event Management

- event title, description, banner, vote price, currency, start date, and end date
- event status lifecycle: `draft`, `published`, `closed`, `cancelled`
- only published events are visible publicly
- only active published events accept votes
- event publish validation requires at least one active nominee, a positive vote price, and a valid date window

### Nominee Management

- nominee name, bio, photo, vote code, display order, and active status
- nominee detail page for public voting
- event-scoped nominee management for the organizer

### Voting and Payments

- guest-first voting with no voter account requirement
- vote quantity purchase model, not one database row per vote
- Paystack-first payment integration
- webhook confirmation is the source of truth for successful votes
- duplicate webhook deliveries must be idempotent

### Leaderboard and Reporting

- leaderboard is computed from successful vote purchases
- public event page refreshes leaderboard through HTMX polling
- organizer dashboard shows total votes, total revenue, latest payments, and nominee rankings

### Wallet and Revenue

- ledger-backed organizer wallet
- platform commission from a single configured rate
- every successful payment posts a balanced ledger transaction
- withdrawals are now supported through organizer requests, admin review, and ledger-backed payout posting

## Success Criteria

- organizer can publish a valid event without admin intervention
- guest voter can complete a payment-to-vote flow successfully
- leaderboard totals match successful vote purchases
- organizer revenue matches ledger postings
- duplicate webhooks do not create duplicate votes or duplicate ledger entries
- organizers can opt into SMS and receive event and withdrawal updates

## Acceptance Scenarios

- organizer creates an event, adds nominees, publishes it, and sees a public microsite
- voter completes a Paystack payment and the selected nominee gains votes
- organizer dashboard reflects vote and revenue changes after payment confirmation
- admin can inspect events, payments, vote purchases, and ledger entries in Django admin

## Later Phases

VoteCentral will expand from the paid-competition MVP into:

- public polls
- secure elections
- OTP and token-based voter verification
- WhatsApp notifications
- WebSocket leaderboards
- stronger anti-fraud tooling
- multi-tenant organizations and team management

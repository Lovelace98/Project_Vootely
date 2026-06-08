# VoteCentral — Frontend Design Specification (Updated)

## 1. Visual Identity & Theme
This design follows a "Modern Clean" aesthetic with a card-based layout, soft rounded corners (16px to 24px), and high-contrast branding.

### Brand Color Palette
- **Primary Blue (`#191bdf`):** Used for primary navigation and active progress fills.
- **Accent Orange (`#fe6807`):** Used for "Call to Action" buttons (e.g., *Withdraw*, *Create Event*), alerts, and highlighting revenue metrics.
- **Deep Dark (`#09080d`):** Primary text color and sidebar backgrounds.
- **Surface:** Light-grey (`#f8fafc`) workspace to allow white cards to stand out.

---

## 2. Layout Structure

### A. Profile & Navigation (Left Sidebar)
* **Profile Card:** - User Avatar: Circular with a Blue border.
    - Info: **Lovelace | Organizer**.
    - Wallet Badge: Pill-shaped orange badge showing current balance.
* **Menu Items:** Dashboard, My Events, Nominees, Wallet, Analytics, Logs.

### B. Recent Activities & Notifications (Right Sidebar)
*This replaces the "Onboarding Task" card from the reference image.*
* **Live Activity Feed:** A scrollable vertical list of the most recent system events:
    - **Success State (Blue):** "Vote confirmed for Nominee: Kofi (Competition: Summer Awards)."
    - **Financial State (Orange):** "Payment of GHS 50.00 received via Mobile Money."
    - **Security Alert:** "Suspicious activity flagged: Duplicate IP detected (Anti-Fraud)."
    - **System:** "Withdrawal request for GHS 1,200 approved."

---

## 3. Dashboard Component Breakdown

### Row 1: KPI Stats
Pill-style horizontal cards:
- **Total Votes:** Live counter with a small sparkline.
- **Revenue:** Large figure in Deep Dark text.
- **Active Polls:** Count of live events.

### Row 2: Analytics & Real-Time Monitoring
* **Voting Trends:** A large card featuring a Primary Blue area chart showing votes over the last 24 hours.
* **Live Clock:** A circular widget showing time remaining for the "Top Featured Event."

### Row 3: Planning & History
* **Event Calendar:** A weekly view showing start/end dates for upcoming competitions.
* **Quick Actions:** Large Orange buttons for "New Competition" or "Export Analytics."

---

## 4. Technical Implementation Notes
- **Framework:** TailwindCSS + DaisyUI components.
- **Real-Time:** The **Recent Activity Feed** should be updated via WebSockets (Redis) to ensure the UI updates instantly without page refreshes.
- **Responsiveness:** On mobile, the **Recent Activities** sidebar should move to a bottom-sheet or a dedicated "Updates" tab.
document.addEventListener("DOMContentLoaded", () => {
    // Guard against duplicate initialization (e.g. from HTMX boosted loads)
    if (window.vootelyTourStarted) {
        return;
    }
    window.vootelyTourStarted = true;

    // Only run if the driver library is loaded and the onboarding URL is defined
    if (typeof window.driver === 'undefined' || !window.VOOTELY_ONBOARDING_URL) {
        return;
    }

    let onboardingCompletionSent = false;

    function markOnboardingCompleted() {
        if (onboardingCompletionSent) {
            return;
        }

        onboardingCompletionSent = true;

        const url = window.VOOTELY_ONBOARDING_URL;
        const csrfToken = window.VOOTELY_ONBOARDING_CSRF_TOKEN;

        fetch(url, {
            method: 'POST',
            credentials: 'same-origin',
            keepalive: true,
            headers: {
                'X-CSRFToken': csrfToken,
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({})
        })
        .then(response => {
            if (!response.ok) {
                console.error("Failed to mark onboarding tour completed on server.");
            }
        })
        .catch(error => {
            console.error("Error communicating with onboarding completion endpoint:", error);
        });
    }

    const driver = window.driver.js.driver;

    const tour = driver({
        showProgress: true,
        animate: true,
        showButtons: ['next', 'previous'],
        nextBtnText: 'Next',
        prevBtnText: 'Previous',
        doneBtnText: 'Done',
        popoverClass: 'driverjs-theme',
        steps: [
            {
                element: '#tour-brand-logo',
                popover: {
                    title: 'Welcome to Vootely',
                    description: "Let's take a quick walk through your new organizer dashboard so you know exactly where everything is.",
                    side: "right",
                    align: 'start'
                }
            },
            {
                element: '#tour-nav-dashboard',
                popover: {
                    title: 'Dashboard overview',
                    description: 'Your home screen. Get quick stats on recent payments, overall revenue, and active voting/ticketing events.',
                    side: "right",
                    align: 'start'
                }
            },
            {
                element: '#tour-nav-competitions',
                popover: {
                    title: 'Paid voting competitions',
                    description: 'Set up public contests where fans buy votes (via Paystack, cards, or offline USSD shortcodes). Monitor nominee standings and custom bulk bundles.',
                    side: "right",
                    align: 'start'
                }
            },
            {
                element: '#tour-nav-elections',
                popover: {
                    title: 'Secure organizational elections',
                    description: 'Run formal ballots with locked voter lists, automated credential tokens, and secret audit tallies.',
                    side: "right",
                    align: 'start'
                }
            },
            {
                element: '#tour-nav-tickets',
                popover: {
                    title: 'Event ticket sales',
                    description: 'Create multi-tier tickets (VIP, Regular, etc.), track orders, and issue usher check-in passes for door entry.',
                    side: "right",
                    align: 'start'
                }
            },
            {
                element: '#tour-nav-revenue',
                popover: {
                    title: 'Financial ledger',
                    description: 'Analyze all your transaction earnings, commission breakdown, and ledger balance in one place.',
                    side: "right",
                    align: 'start'
                }
            },
            {
                element: '#tour-nav-withdrawals',
                popover: {
                    title: 'Request withdrawals',
                    description: 'Initiate payouts directly to your Mobile Money wallet or bank account once your earnings settle.',
                    side: "right",
                    align: 'start'
                }
            },
            {
                element: '#tour-search-trigger',
                popover: {
                    title: 'Search workspace',
                    description: 'Press Cmd+K or click this bar to search across all your elections, nominees, tickets, and payment attempts instantly.',
                    side: "bottom",
                    align: 'end'
                }
            },
            {
                element: '#tour-profile-pill',
                popover: {
                    title: 'Profile and account',
                    description: 'Open your profile to manage organizer details, avatar, contact information, and account preferences.',
                    side: "bottom",
                    align: 'end'
                }
            },
            {
                element: '#tour-notifications-bell',
                popover: {
                    title: 'Notifications',
                    description: 'Use the notification bell to review recent payment, event, ticketing, election, and withdrawal updates.',
                    side: "bottom",
                    align: 'end'
                }
            }
        ],
        onDestroyStarted: () => {
            // Fired if they skip, exit, or finish the tour
            markOnboardingCompleted();
            tour.destroy();
        }
    });

    // Start the tour after a slight delay to allow dashboard animations to finish smoothly
    setTimeout(() => {
        tour.drive();
    }, 800);
});

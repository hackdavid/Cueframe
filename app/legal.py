"""Terms, privacy, refunds, acceptable use and contact pages (owner to review before launch)."""

UPDATED = "26 October 2026"


def docs(email: str) -> dict[str, dict]:
    return {
        "terms": {
            "title": "Terms of service",
            "intro": "These terms govern your use of Cueframe's website, app and API. By "
            "creating an account or using Cueframe you agree to them.",
            "sections": [
                (
                    "1. The service",
                    "Cueframe turns your requests, photos, links and files into "
                    "narrated videos. Maths lessons are checked automatically, and software "
                    "tutorials are recorded from real runs in a cloud sandbox. Automated checks "
                    "reduce mistakes but cannot guarantee that every video is error-free; check "
                    "important results yourself.",
                ),
                (
                    "2. Accounts",
                    "You must give accurate details and keep your login secure. You "
                    "are responsible for activity on your account. Users under 13, or under the "
                    "age of digital consent where they live, need a parent or guardian to create "
                    "and manage the account.",
                ),
                (
                    "3. Plans, billing and renewals",
                    "Paid plans are billed in advance, monthly or "
                    "yearly, and renew automatically until cancelled. Monthly video allowances "
                    "reset each billing period and do not roll over, except video packs, which "
                    "don't expire. We may change prices with at least 30 days' notice; changes "
                    "apply from your next renewal.",
                ),
                (
                    "4. Your content",
                    "You keep all rights to what you upload and to the videos "
                    "Cueframe makes for you. You give us a limited licence to process, store and "
                    "display that content only to provide the service to you. Paid plans include "
                    "commercial use of your videos. Free videos carry a 'Made with Cueframe' end "
                    "card.",
                ),
                (
                    "5. Acceptable use",
                    "You agree to follow our Acceptable use policy. We may "
                    "suspend accounts that break it, put the service or other users at risk, or "
                    "don't pay.",
                ),
                (
                    "6. Availability",
                    "We aim for high availability but the service is provided "
                    "'as is'. Rendering depends on third-party infrastructure and may be delayed.",
                ),
                (
                    "7. Liability",
                    "To the extent the law allows, our total liability for any "
                    "claim is limited to the amount you paid us in the 12 months before it. We are "
                    "not liable for indirect or consequential losses. Nothing here limits rights "
                    "you have under consumer law.",
                ),
                (
                    "8. Ending",
                    "You can close your account at any time. We may end these terms "
                    "with notice if we stop offering the service, with a pro-rata refund of any "
                    "prepaid period.",
                ),
                (
                    "9. Changes",
                    "We'll tell you about material changes to these terms by email or "
                    "in the app at least 14 days before they take effect.",
                ),
                ("10. Contact", f"Questions about these terms: {email}."),
            ],
        },
        "privacy": {
            "title": "Privacy policy",
            "intro": "This policy explains what Cueframe collects, why, and the choices you "
            "have. We collect as little as we need to make your videos.",
            "sections": [
                (
                    "What we collect",
                    "Account details (name, email, login); what you send us "
                    "(chat messages, photos, files, links); the videos, narration and captions we "
                    "make; billing records from our payment provider (we never see full card "
                    "numbers); and basic usage and device logs for security and debugging.",
                ),
                (
                    "How we use it",
                    "To make, store and show your videos; to run your account and "
                    "billing; to keep the service secure and prevent abuse; and "
                    "to improve quality, "
                    "using aggregated data. We don't sell your personal data and don't use your "
                    "uploads to advertise to you.",
                ),
                (
                    "AI processing",
                    "Your requests and uploads are sent to the AI, voice and "
                    "cloud-compute providers that make your video, under "
                    "contracts that limit their "
                    "use to providing the service.",
                ),
                (
                    "Service providers",
                    "Cloud hosting and storage (e.g. Cloudflare R2), sandbox "
                    "compute (e.g. Modal), AI model and voice providers (e.g. ElevenLabs), payment "
                    "processing (e.g. Stripe) and email delivery.",
                ),
                (
                    "Storage and security",
                    "Files and videos are stored privately and shared only "
                    "through short-lived signed links. Sandboxes are shut down after each job.",
                ),
                (
                    "Retention",
                    "Uploaded photos and files are kept only as long as needed for "
                    "your videos and can be deleted by you at any time. Videos are kept while your "
                    "account is open. Deleted data is removed from backups within 30 days.",
                ),
                (
                    "Children",
                    "Lessons for children should be requested by a parent, guardian or "
                    "teacher. We don't knowingly collect more data about children than the service "
                    "needs, and we don't profile children.",
                ),
                (
                    "Your rights",
                    "You can access, correct, export or delete your data, and object "
                    f"to or restrict some processing, by emailing {email}. You "
                    f"can also complain to "
                    "your local data-protection authority.",
                ),
                (
                    "Cookies",
                    "We use essential cookies for login and security only. If we add "
                    "analytics, we'll ask first where the law requires it.",
                ),
                ("Changes", "We'll post updates here and email you about significant changes."),
            ],
        },
        "refunds": {
            "title": "Refunds & cancellation",
            "intro": "Cancel any time; here is how refunds work.",
            "sections": [
                (
                    "Cancelling",
                    "Cancel from your account. Your plan stays active until the end "
                    "of the period you've paid for and won't renew.",
                ),
                (
                    "First purchase",
                    "If you're not happy with your first paid month or year, ask "
                    f"within 7 days at {email} for a full refund, provided you've used fewer than "
                    "5 paid videos.",
                ),
                (
                    "Failed videos",
                    "Videos that fail or are refused don't count against your allowance.",
                ),
                ("Video packs", "Unused packs can be refunded within 14 days of purchase."),
                (
                    "Consumer rights",
                    "These terms don't affect any rights you have under local consumer law.",
                ),
            ],
        },
        "acceptable-use": {
            "title": "Acceptable use policy",
            "intro": "Cueframe runs real software for you in cloud sandboxes, so a few rules "
            "keep it safe for everyone.",
            "sections": [
                (
                    "Don't",
                    "Use Cueframe for anything illegal; to harass, deceive or impersonate "
                    "people; to make sexual content involving minors or non-consensual content; to "
                    "attack, scan or overload systems you don't own; to mine cryptocurrency or run "
                    "workloads unrelated to making videos; or to break the rules of the sites a "
                    "tutorial uses.",
                ),
                (
                    "Academic honesty",
                    "Lessons are for learning. Follow your school's rules on "
                    "getting help with assessed work.",
                ),
                (
                    "Copyright",
                    "Only upload files and links you have the right to use. We act on "
                    "valid takedown notices sent to the contact address.",
                ),
                (
                    "Limits",
                    "Fair-use limits apply to unlimited plans so the service stays fast "
                    "for everyone.",
                ),
                (
                    "Enforcement",
                    "We may refuse a request, remove content or suspend an account "
                    "that breaks this policy.",
                ),
            ],
        },
        "contact": {
            "title": "Contact & support",
            "intro": f"We usually reply within one working day. Email {email}.",
            "sections": [
                (
                    "Support",
                    "Problems with a video, your account or billing: include the link "
                    "to the chat or video so we can find it quickly.",
                ),
                (
                    "Creators and partnerships",
                    "Want to join the creator program, or use "
                    "Cueframe for your school, institute or team? Tell us about your audience.",
                ),
                (
                    "Privacy and legal",
                    "Data requests, takedown notices and legal questions go to "
                    "the same address with 'Privacy' or 'Legal' in the subject.",
                ),
            ],
        },
    }

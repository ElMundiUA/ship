# What is a webhook?

A webhook is a URL on your side that a provider calls when something happens. Imagine the alternative: Ship would have to ask GitHub every minute, "Anything new? Any new commits? Any new PRs?" That's wasteful and slow. Instead, GitHub *tells* Ship "a new PR was opened" by making a POST request to a webhook URL that Ship provides. Webhooks are how Ship learns about new commits, new issues, state changes in Linear, new messages in Slack—in real time, instead of waiting for a scheduled check.

When the wizard asks for a webhook URL, it's giving Ship a place to receive these notifications from the provider. The provider stores that URL and calls it whenever there's an event. To ensure that Ship trusts the notification actually came from the provider (and not from someone pretending to be the provider), the provider includes an HMAC signature on each call—a cryptographic stamp that proves the message came from them. Ship verifies the signature before processing the event.

Back to [Appendix index](/docs/appendix)

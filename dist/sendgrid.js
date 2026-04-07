/**
 * SendGrid email for "Ready for human review" notifications.
 * Requires SENDGRID_API_KEY in env. Sender should be verified in SendGrid.
 */
const SENDGRID_API = "https://api.sendgrid.com/v3/mail/send";
export async function sendInReviewNotification(params) {
    const apiKey = process.env.SENDGRID_API_KEY;
    if (!apiKey) {
        console.warn("SENDGRID_API_KEY not set, skipping email notification");
        return false;
    }
    const from = process.env.SENDGRID_FROM_EMAIL ?? "noreply@elmundi.com";
    const fromName = process.env.SENDGRID_FROM_NAME ?? "ElMundi Release";
    const subject = `[${params.issueId}] Ready for review`;
    const html = `
    <h2>Ticket ${params.issueId} ready for human review</h2>
    <p><strong>Preview (check):</strong> <a href="${params.previewUrl}">${params.previewUrl}</a></p>
    <p><strong>Approve (PR):</strong> <a href="${params.prUrl}">${params.prUrl}</a></p>
  `.trim();
    const body = {
        personalizations: [{ to: [{ email: params.to }] }],
        from: { email: from, name: fromName },
        subject,
        content: [{ type: "text/html", value: html }],
    };
    try {
        const res = await fetch(SENDGRID_API, {
            method: "POST",
            headers: {
                Authorization: `Bearer ${apiKey}`,
                "Content-Type": "application/json",
            },
            body: JSON.stringify(body),
        });
        if (!res.ok) {
            const err = await res.text();
            console.error("SendGrid error:", res.status, err);
            return false;
        }
        return true;
    }
    catch (e) {
        console.error("SendGrid send failed:", e);
        return false;
    }
}
//# sourceMappingURL=sendgrid.js.map
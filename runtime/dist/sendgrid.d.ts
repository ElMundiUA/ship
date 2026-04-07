/**
 * SendGrid email for "Ready for human review" notifications.
 * Requires SENDGRID_API_KEY in env. Sender should be verified in SendGrid.
 */
export interface InReviewEmailParams {
    to: string;
    issueId: string;
    previewUrl: string;
    prUrl: string;
}
export declare function sendInReviewNotification(params: InReviewEmailParams): Promise<boolean>;
//# sourceMappingURL=sendgrid.d.ts.map
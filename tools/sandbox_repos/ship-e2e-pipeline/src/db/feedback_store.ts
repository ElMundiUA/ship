export type Feedback = {
  id: string;
  author: string;
  message: string;
  createdAt: string;
};

type NewFeedback = Omit<Feedback, "id" | "createdAt">;

export class FeedbackStore {
  private rows = new Map<string, Feedback>();
  private counter = 0;

  add(input: NewFeedback): Feedback {
    this.counter += 1;
    const row: Feedback = {
      id: `fb_${this.counter}`,
      author: input.author,
      message: input.message,
      createdAt: new Date().toISOString(),
    };
    this.rows.set(row.id, row);
    return row;
  }

  get(id: string): Feedback | undefined {
    return this.rows.get(id);
  }

  list(): Feedback[] {
    return Array.from(this.rows.values()).sort((a, b) =>
      a.createdAt.localeCompare(b.createdAt),
    );
  }

  remove(id: string): boolean {
    return this.rows.delete(id);
  }
}

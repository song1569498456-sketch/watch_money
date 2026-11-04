export type QueryToken = { version: number; context: string; controller: AbortController };

/** Invalidate during render; abort on effect cleanup, never let an old callback commit. */
export class QueryGeneration {
  version = 0;
  private context = "";
  private active = false;
  private attempt = 0;
  private current: QueryToken | null = null;

  update(context: string, active: boolean, attempt = 0) {
    if (context !== this.context || active !== this.active || attempt !== this.attempt) {
      this.version += 1;
      this.context = context;
      this.active = active;
      this.attempt = attempt;
    }
    return this.version;
  }

  begin(): QueryToken {
    this.cancelCurrent();
    const token = { version: this.version, context: this.context, controller: new AbortController() };
    this.current = token;
    return token;
  }

  accepts(token: QueryToken) {
    return this.active && this.current === token && token.version === this.version &&
      token.context === this.context && !token.controller.signal.aborted;
  }

  cancel(token: QueryToken) {
    token.controller.abort();
    if (this.current === token) this.current = null;
  }

  cancelCurrent() {
    if (this.current) this.cancel(this.current);
  }
}

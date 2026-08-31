declare module "jest-axe" {
  export function axe(
    container: Element
  ): Promise<{ violations: Array<{ impact?: string | null; id: string; description: string }> }>;
}

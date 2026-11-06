import { href } from "./lib";
import { Badge, ButtonLink, Card } from "./ui";

export function DemoContext({ step }: { step: "agents" | "workflow" }) {
  return <Card>
    <Badge>Demo contextual entry</Badge>
    <p>{step === "agents"
      ? "This is the existing Ask workspace. Review the question and confirm any paid call. Answers and conversations stay account-scoped; the assistant is read-only. Shared-memory learning and conversation deletion are outside this tour."
      : "This is the existing shared Work workspace, not a disposable demo. Inspect prerequisites and history before a deliberate action; saved task changes may advance the process."}</p>
    <ButtonLink href={href("/demo", { step })}>Return to Demo</ButtonLink>
  </Card>;
}

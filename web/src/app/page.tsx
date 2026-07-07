import Chat from "@/components/Chat";

export default function Home() {
  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col gap-4 p-4">
      <header>
        <h1 className="text-2xl font-bold">
          Automate This — SMB Automation Advisor
        </h1>
        <p className="text-sm text-gray-500">
          Functional demo — production design pending.
        </p>
      </header>
      <Chat />
    </main>
  );
}

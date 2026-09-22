import { useEffect, useState } from "react";
import { fetchHealth } from "./api/health";

type BackendStatus = "checking" | "connected" | "unavailable";

function App() {
  const [status, setStatus] = useState<BackendStatus>("checking");

  useEffect(() => {
    fetchHealth()
      .then(() => setStatus("connected"))
      .catch(() => setStatus("unavailable"));
  }, []);

  return (
    <main>
      <h1>Polyglot</h1>
      <p>Multi-Provider AI Workbench</p>
      <p>
        Backend:{" "}
        {status === "checking" && "Checking..."}
        {status === "connected" && "Connected"}
        {status === "unavailable" && "Unavailable"}
      </p>
    </main>
  );
}

export default App;

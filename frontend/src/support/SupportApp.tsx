import { Navigate, Route, Routes } from "react-router-dom";
import { Sidebar } from "../Sidebar";
import { useAuth } from "../auth";
import { Contacts } from "./Contacts";
import { Categories } from "./Categories";
import { TicketQueue, TicketCreate, TicketDetail } from "./Tickets";
import { Operations } from "./Operations";
import { Integrations } from "./Integrations";

export function SupportApp() {
  const { current } = useAuth();
  const org = current?.organizations.find(
    (value) => value.id === current.selected_organization_id,
  );
  return (
    <div className="workspace-shell">
      <Sidebar />
      <main className="workspace-main support-main" key={org?.id ?? "none"}>
        {org ? (
          <Routes>
            <Route path="tickets" element={<TicketQueue org={org} />} />
            <Route path="tickets/new" element={<TicketCreate org={org} />} />
            <Route path="tickets/:id" element={<TicketDetail org={org} />} />
            <Route path="contacts" element={<Contacts org={org} />} />
            <Route path="categories" element={<Categories org={org} />} />
            <Route path="operations" element={<Operations org={org} />} />
            <Route path="integrations" element={<Integrations org={org} />} />
            <Route path="*" element={<Navigate to="/app/tickets" replace />} />
          </Routes>
        ) : (
          <section className="panel">
            <h1>No workspace selected</h1>
            <p>
              Select another workspace or <a href="/app?new=1">create one</a>.
            </p>
          </section>
        )}
      </main>
    </div>
  );
}

import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { Link } from "react-router-dom";
import { api, ApiError, type CurrentUser } from "./api";

type AuthState = {
  current: CurrentUser | null;
  loading: boolean;
  setCurrent: (value: CurrentUser | null) => void;
  reload: () => Promise<void>;
};
const Auth = createContext<AuthState | null>(null);
export function useAuth() {
  const auth = useContext(Auth);
  if (!auth) throw new Error("Missing account context");
  return auth;
}
export const message = (error: unknown) =>
  error instanceof Error
    ? error.message
    : "Something went wrong. Please retry.";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [current, setCurrent] = useState<CurrentUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [failure, setFailure] = useState("");
  async function reload() {
    setCurrent(await api<CurrentUser>("/auth/me"));
  }
  useEffect(() => {
    let active = true;
    const expired = () => setCurrent(null);
    window.addEventListener("session-expired", expired);
    void api<CurrentUser>("/auth/me")
      .then((value) => {
        if (active) setCurrent(value);
      })
      .catch((error) => {
        if (active && !(error instanceof ApiError && error.status === 401))
          setFailure(message(error));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
      window.removeEventListener("session-expired", expired);
    };
  }, []);
  if (failure)
    return (
      <main>
        <header>SupportFlow AI</header>
        <div className="card">
          <ErrorText text={failure} />
          <button onClick={() => window.location.reload()}>
            Retry connection
          </button>
          <p>
            <Link to="/status">System status</Link>
          </p>
        </div>
      </main>
    );
  return (
    <Auth.Provider value={{ current, loading, setCurrent, reload }}>
      {children}
    </Auth.Provider>
  );
}

export function ErrorText({ text }: { text: string }) {
  return text ? (
    <p className="error" role="alert">
      {text}
    </p>
  ) : null;
}

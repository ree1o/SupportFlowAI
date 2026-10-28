import { useEffect, useState } from "react";
import { api } from "../api";
import { message } from "../auth";

export function useRemote<T>(path: string | null) {
  const [revision, setRevision] = useState(0);
  const [resource, setResource] = useState<{
    path: string | null;
    data?: T;
    error: string;
    loading: boolean;
  }>({ path, error: "", loading: true });
  useEffect(() => {
    if (path === null) {
      setResource({ path, error: "", loading: false });
      return;
    }
    let active = true;
    setResource((previous) => ({
      path,
      data: previous.path === path ? previous.data : undefined,
      error: "",
      loading: true,
    }));
    void api<T>(path)
      .then((data) => {
        if (active) setResource({ path, data, error: "", loading: false });
      })
      .catch((error) => {
        if (active)
          setResource({ path, error: message(error), loading: false });
      });
    return () => {
      active = false;
    };
  }, [path, revision]);
  return {
    data: resource.path === path ? resource.data : undefined,
    error: resource.path === path ? resource.error : "",
    loading: resource.path !== path || resource.loading,
    reload: () => setRevision((value) => value + 1),
  };
}

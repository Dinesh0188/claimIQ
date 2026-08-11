"use client";

import { useQuery } from "@tanstack/react-query";
import { claimiqApi } from "@/lib/api";
import { createContext, useContext, useState } from "react";

interface ProfileContextValue {
  profile: string;
  setProfile: (p: string) => void;
}

const ProfileContext = createContext<ProfileContextValue>({
  profile: "typical",
  setProfile: () => {},
});

export function useProfile() {
  return useContext(ProfileContext);
}

export function ProfileProvider({ children }: { children: React.ReactNode }) {
  const [profile, setProfile] = useState(() => {
    if (typeof window !== "undefined") {
      return localStorage.getItem("ciq.profile") || "typical";
    }
    return "typical";
  });

  const handleSetProfile = (p: string) => {
    setProfile(p);
    if (typeof window !== "undefined") {
      localStorage.setItem("ciq.profile", p);
    }
  };

  return (
    <ProfileContext.Provider value={{ profile, setProfile: handleSetProfile }}>
      {children}
    </ProfileContext.Provider>
  );
}

export function ProfileSelector() {
  const { profile, setProfile } = useProfile();
  const { data: profiles } = useQuery({
    queryKey: ["profiles"],
    queryFn: claimiqApi.profiles,
  });

  if (!profiles) return null;

  return (
    <div className="space-y-1">
      <label className="text-xs text-muted-2 block">Insurer interpretation</label>
      <select
        value={profile}
        onChange={(e) => setProfile(e.target.value)}
        className="w-full bg-panel-3 border border-line rounded px-2 py-1.5 text-xs text-white focus:outline-none focus:border-accent"
      >
        {Object.entries(profiles).map(([key, p]) => (
          <option key={key} value={key}>
            {p.label}
          </option>
        ))}
      </select>
    </div>
  );
}

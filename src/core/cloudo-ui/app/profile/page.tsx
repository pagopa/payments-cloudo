"use client";

import { useState, useEffect } from "react";
import Image from "next/image";
import { cloudoFetch } from "@/lib/api";
import { useRouter } from "next/navigation";
import {
  HiOutlineUser,
  HiOutlineSave,
  HiOutlineRefresh,
  HiOutlineCheckCircle,
  HiOutlineExclamationCircle,
  HiOutlineShieldCheck,
  HiOutlineX,
  HiOutlineKey,
  HiOutlineEye,
  HiOutlineEyeOff,
  HiOutlineClipboardCopy,
  HiOutlineClipboardCheck,
} from "react-icons/hi";

interface Notification {
  id: string;
  type: "success" | "error";
  message: string;
}

interface User {
  username: string | null;
  email: string | null;
  password: string | null;
  role: string | null;
  picture?: string;
  sso_provider?: string;
  team?: string;
}

export default function ProfilePage() {
  const router = useRouter();
  const [user, setUser] = useState<User>();
  const [profile, setProfile] = useState({
    username: "",
    email: "",
    password: "",
    confirmPassword: "",
    api_token: "",
    picture: "",
    sso_provider: "",
    team: "default",
  });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [notifications, setNotifications] = useState<Notification[]>([]);

  const [showToken, setShowToken] = useState(false);
  const [showRotateModal, setShowRotateModal] = useState(false);
  const [copied, setCopied] = useState(false);

  const addNotification = (type: "success" | "error", message: string) => {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    setNotifications((prev) => [...prev, { id, type, message }]);
    setTimeout(() => {
      setNotifications((prev) => prev.filter((n) => n.id !== id));
    }, 4000);
  };

  const removeNotification = (id: string) => {
    setNotifications((prev) => prev.filter((n) => n.id !== id));
  };

  useEffect(() => {
    const userData = localStorage.getItem("cloudo_user");
    if (userData) {
      try {
        const parsedUser = JSON.parse(userData);
        setUser(parsedUser);
        fetchProfile();
      } catch {
        router.push("/login");
      }
    } else {
      router.push("/login");
    }
  }, [router]);

  const fetchProfile = async () => {
    setLoading(true);
    try {
      const res = await cloudoFetch(`/auth/profile`);
      if (res.ok) {
        const data = await res.json();
        const nextUsername = data.username ?? "";
        const nextEmail = data.email ?? "";
        const nextApiToken = data.api_token ?? "";
        const nextPicture = data.picture ?? "";
        const nextSsoProvider = data.sso_provider ?? "";
        const nextTeam = data.team ?? "default";
        setProfile((prev) => ({
          ...prev,
          username: nextUsername,
          email: nextEmail,
          api_token: nextApiToken,
          picture: nextPicture,
          sso_provider: nextSsoProvider,
          team: nextTeam,
        }));
        // Synchronize local storage user if it changed
        const userData = localStorage.getItem("cloudo_user");
        if (userData) {
          const u = JSON.parse(userData);
          let updated = false;
          if (u.picture !== nextPicture) {
            u.picture = nextPicture;
            updated = true;
          }
          if (u.sso_provider !== nextSsoProvider) {
            u.sso_provider = nextSsoProvider;
            updated = true;
          }
          if (u.team !== nextTeam) {
            u.team = nextTeam;
            updated = true;
          }
          if (updated) {
            localStorage.setItem("cloudo_user", JSON.stringify(u));
            setUser(u);
            // Trigger a storage event manually to notify other components (like Sidebar)
            window.dispatchEvent(new Event("storage"));
          }
        }
      }
    } catch (err) {
      console.error("Failed to fetch profile", err);
    } finally {
      setLoading(false);
    }
  };

  const isGoogleUser =
    profile.sso_provider === "google" || user?.sso_provider === "google";

  const handleSaveProfile = async (e: React.FormEvent) => {
    if (isGoogleUser) return;
    e.preventDefault();
    if (profile.password && profile.password !== profile.confirmPassword) {
      addNotification("error", "Passwords do not match");
      return;
    }

    setSaving(true);
    try {
      const res = await cloudoFetch(`/auth/profile`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: profile.email,
          password: profile.password || undefined,
        }),
      });

      if (res.ok) {
        addNotification("success", "Profile updated successfully");
        setProfile((prev) => ({ ...prev, password: "", confirmPassword: "" }));
        // Update local storage email if it changed
        const userData = localStorage.getItem("cloudo_user");
        if (userData) {
          const u = JSON.parse(userData);
          u.email = profile.email;
          localStorage.setItem("cloudo_user", JSON.stringify(u));
        }
      } else {
        const data = await res.json();
        addNotification("error", data.error || "Failed to update profile");
      }
    } catch {
      addNotification("error", "Uplink failed");
    } finally {
      setSaving(false);
    }
  };

  const handleGenerateToken = async () => {
    if (profile.api_token) {
      setShowRotateModal(true);
      return;
    }
    await executeGenerateToken();
  };

  const executeGenerateToken = async () => {
    setSaving(true);
    setShowRotateModal(false);
    try {
      const res = await cloudoFetch(`/auth/profile`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          generate_token: true,
        }),
      });

      if (res.ok) {
        const data = await res.json();
        setProfile((prev) => ({ ...prev, api_token: data.api_token ?? "" }));
        addNotification("success", "New API Token generated");
      } else {
        const data = await res.json();
        addNotification("error", data.error || "Failed to generate token");
      }
    } catch {
      addNotification("error", "Uplink failed");
    } finally {
      setSaving(false);
    }
  };

  const copyToClipboard = () => {
    if (profile.api_token) {
      navigator.clipboard.writeText(profile.api_token);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  return (
    <div className="flex flex-col h-full bg-cloudo-dark text-cloudo-text font-mono selection:bg-cloudo-accent/30">
      {/* Rotate Token Confirmation Modal */}
      {showRotateModal && (
        <div className="fixed inset-0 z-[110] flex items-center justify-center p-4 bg-cloudo-dark/80 backdrop-blur-sm animate-in fade-in duration-300">
          <div className="bg-cloudo-panel border border-cloudo-border max-w-md w-full p-8 shadow-2xl relative overflow-hidden">
            {/* Decorative corner */}
            <div className="absolute top-0 right-0 w-12 h-12 overflow-hidden pointer-events-none">
              <div className="absolute top-[-24px] right-[-24px] w-12 h-12 bg-cloudo-err rotate-45" />
            </div>

            <div className="flex flex-col gap-6">
              <div className="flex items-center gap-4">
                <div className="p-3 bg-cloudo-err/10 border border-cloudo-err/20">
                  <HiOutlineRefresh className="text-cloudo-err w-6 h-6" />
                </div>
                <div>
                  <h3 className="text-lg font-black text-cloudo-text uppercase tracking-widest">
                    Rotate API Token
                  </h3>
                  <p className="text-[10px] text-cloudo-err font-bold uppercase tracking-widest">
                    Security Warning // ACTION_REQUIRED
                  </p>
                </div>
              </div>

              <div className="space-y-4">
                <p className="text-[11px] text-cloudo-text/80 leading-relaxed uppercase tracking-widest font-bold">
                  You are about to generate a new API token. The current token
                  will be{" "}
                  <span className="text-cloudo-err">
                    immediately invalidated
                  </span>
                  .
                </p>
                <p className="text-[10px] text-cloudo-muted leading-relaxed uppercase">
                  Any external systems or scripts currently using the existing
                  token will lose access until updated with the new credentials.
                </p>
              </div>

              <div className="flex gap-4 pt-4 border-t border-cloudo-border/50">
                <button
                  onClick={() => setShowRotateModal(false)}
                  className="flex-1 btn btn-ghost h-11 border border-cloudo-border text-[11px] font-black uppercase tracking-widest"
                >
                  Cancel
                </button>
                <button
                  onClick={executeGenerateToken}
                  className="flex-1 btn bg-cloudo-err hover:bg-cloudo-err/80 text-white h-11 text-[11px] font-black uppercase tracking-widest"
                >
                  Confirm Rotation
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Notifications */}
      <div className="fixed top-8 right-8 z-[100] flex flex-col gap-3 pointer-events-none">
        {notifications.map((n) => (
          <div
            key={n.id}
            className={`px-6 py-4 flex items-center gap-4 animate-in slide-in-from-right-full duration-300 border shadow-2xl pointer-events-auto min-w-[300px] relative overflow-hidden ${
              n.type === "success"
                ? "bg-cloudo-panel border-cloudo-ok/30 text-cloudo-ok"
                : "bg-cloudo-panel border-cloudo-err/30 text-cloudo-err"
            }`}
          >
            {/* Background Accent */}
            <div
              className={`absolute top-0 left-0 w-1 h-full ${
                n.type === "success" ? "bg-cloudo-ok" : "bg-cloudo-err"
              }`}
            />

            <div
              className={`p-2 ${
                n.type === "success" ? "bg-cloudo-ok/10" : "bg-cloudo-err/10"
              } shrink-0`}
            >
              {n.type === "success" ? (
                <HiOutlineCheckCircle className="w-5 h-5" />
              ) : (
                <HiOutlineExclamationCircle className="w-5 h-5" />
              )}
            </div>

            <div className="flex flex-col gap-1 flex-1">
              <span className="text-[10px] font-black uppercase tracking-[0.2em]">
                {n.type === "success" ? "System Success" : "Engine Error"}
              </span>
              <span className="text-[11px] font-bold text-cloudo-text/90 uppercase tracking-widest leading-tight">
                {n.message}
              </span>
            </div>

            <button
              onClick={() => removeNotification(n.id)}
              className="p-1 hover:bg-white/5 transition-colors opacity-40 hover:opacity-100"
            >
              <HiOutlineX className="w-3.5 h-3.5" />
            </button>
          </div>
        ))}
      </div>

      {/* Top Bar */}
      <div className="flex items-center justify-between px-8 py-4 border-b border-cloudo-border bg-cloudo-panel sticky top-0 z-20">
        <div className="flex items-center gap-4 shrink-0">
          <div className="p-2 bg-cloudo-accent/5 border border-cloudo-accent/20 shrink-0">
            <HiOutlineUser className="text-cloudo-accent w-5 h-5" />
          </div>
          <div>
            <h1 className="text-sm font-black tracking-[0.2em] text-cloudo-text uppercase">
              Personal Profile
            </h1>
            <p className="text-[11px] text-cloudo-muted font-bold uppercase tracking-[0.3em] opacity-70">
              Identity Management // PROFILE_GATE
            </p>
          </div>
        </div>

        <div className="flex items-center gap-4">
          <button
            onClick={fetchProfile}
            className="btn btn-ghost h-10 px-4 flex items-center gap-2"
          >
            <HiOutlineRefresh
              className={`w-4 h-4 ${loading ? "animate-spin" : ""}`}
            />
            Sync
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-auto p-8">
        <div className="max-w-4xl mx-auto">
          <div className="bg-cloudo-panel border border-cloudo-border relative overflow-hidden">
            {/* Decorative corner */}
            <div className="absolute top-0 right-0 w-16 h-16 overflow-hidden pointer-events-none">
              <div className="absolute top-[-32px] right-[-32px] w-16 h-16 bg-cloudo-border rotate-45" />
            </div>

            <div className="p-8 border-b border-cloudo-border flex items-center gap-4 bg-cloudo-accent/5">
              {profile.picture ? (
                <Image
                  src={profile.picture}
                  alt={profile.username || "Profile"}
                  width={64}
                  height={64}
                  referrerPolicy="no-referrer"
                  className="w-16 h-16 border-2 border-cloudo-accent/20 object-cover shrink-0"
                />
              ) : (
                <div className="w-16 h-16 bg-cloudo-accent text-cloudo-dark flex items-center justify-center font-black text-2xl uppercase border-2 border-cloudo-accent/20">
                  {profile.username?.slice(0, 2) || "??"}
                </div>
              )}
              <div>
                <h2 className="text-xl font-black text-cloudo-text uppercase tracking-widest">
                  {profile.username || "OPERATOR"}
                </h2>
                <div className="flex items-center gap-2 mt-1">
                  <span className="px-2 py-0.5 border border-cloudo-accent/30 text-[10px] font-black text-cloudo-accent uppercase tracking-widest bg-cloudo-accent/5">
                    {user?.role || "L-GUEST"}
                  </span>
                  <span className="px-2 py-0.5 border border-cloudo-border text-[10px] font-black text-cloudo-muted uppercase tracking-widest">
                    Team: {profile.team || user?.team || "default"}
                  </span>
                  <span className="text-[10px] text-cloudo-muted font-bold uppercase tracking-widest">
                    Authorized Terminal User
                  </span>
                </div>
              </div>
            </div>

            <form onSubmit={handleSaveProfile} className="p-8 space-y-8">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
                <div className="space-y-2">
                  <label className="text-[11px] font-black uppercase tracking-widest text-cloudo-muted ml-1 block">
                    Identity (Username)
                  </label>
                  <input
                    type="text"
                    className="input h-11 w-full opacity-50 cursor-not-allowed bg-cloudo-accent/5"
                    value={profile.username}
                    readOnly
                    disabled
                  />
                  <p className="text-[10px] text-cloudo-muted/70 uppercase tracking-tight ml-1">
                    System identifier cannot be modified
                  </p>
                </div>

                <div className="space-y-2">
                  <label className="text-[11px] font-black uppercase tracking-widest text-cloudo-muted ml-1 block">
                    Email Endpoint
                  </label>
                  <input
                    type="email"
                    className={`input h-11 w-full ${
                      isGoogleUser
                        ? "opacity-50 cursor-not-allowed bg-cloudo-accent/5"
                        : ""
                    }`}
                    value={profile.email}
                    onChange={(e) =>
                      setProfile({ ...profile, email: e.target.value })
                    }
                    readOnly={isGoogleUser}
                    disabled={isGoogleUser}
                    required
                  />
                  {isGoogleUser && (
                    <p className="text-[10px] text-cloudo-muted/70 uppercase tracking-tight ml-1">
                      Email managed by Google SSO provider
                    </p>
                  )}
                </div>

                {!isGoogleUser && (
                  <>
                    <div className="space-y-2">
                      <label className="text-[11px] font-black uppercase tracking-widest text-cloudo-muted ml-1 block">
                        New Password
                      </label>
                      <input
                        type="password"
                        className="input h-11 w-full"
                        placeholder="Leave empty to keep current"
                        value={profile.password}
                        onChange={(e) =>
                          setProfile({ ...profile, password: e.target.value })
                        }
                      />
                    </div>

                    <div className="space-y-2">
                      <label className="text-[11px] font-black uppercase tracking-widest text-cloudo-muted ml-1 block">
                        Confirm New Password
                      </label>
                      <input
                        type="password"
                        className="input h-11 w-full"
                        placeholder="Verify security credentials"
                        value={profile.confirmPassword}
                        onChange={(e) =>
                          setProfile({
                            ...profile,
                            confirmPassword: e.target.value,
                          })
                        }
                      />
                    </div>
                  </>
                )}
              </div>

              <div className="pt-8 border-t border-cloudo-border">
                <div className="flex items-center gap-4 mb-4">
                  <div className="p-2 bg-cloudo-accent/10 border border-cloudo-accent/20">
                    <HiOutlineKey className="text-cloudo-accent w-4 h-4" />
                  </div>
                  <div>
                    <h3 className="text-sm font-black text-cloudo-text uppercase tracking-widest">
                      Personal API Token
                    </h3>
                    <p className="text-[10px] text-cloudo-muted font-bold uppercase tracking-widest">
                      External Integration Endpoint
                    </p>
                  </div>
                </div>

                <div className="space-y-4">
                  <div className="flex gap-2">
                    <div className="relative flex-1">
                      <input
                        type={showToken ? "text" : "password"}
                        className="input h-11 w-full font-mono text-[11px] pr-20"
                        value={profile.api_token || "NO_TOKEN_GENERATED"}
                        readOnly
                      />
                      <div className="absolute right-2 top-1/2 -translate-y-1/2 flex items-center gap-0.5">
                        <button
                          type="button"
                          onClick={copyToClipboard}
                          title="Copy to clipboard"
                          className={`p-1.5 hover:bg-white/5 transition-colors ${
                            copied
                              ? "text-cloudo-ok"
                              : "text-cloudo-muted hover:text-cloudo-accent"
                          }`}
                        >
                          {copied ? (
                            <HiOutlineClipboardCheck className="w-4 h-4" />
                          ) : (
                            <HiOutlineClipboardCopy className="w-4 h-4" />
                          )}
                        </button>
                        <button
                          type="button"
                          onClick={() => setShowToken(!showToken)}
                          title={showToken ? "Hide token" : "Show token"}
                          className="p-1.5 hover:bg-white/5 text-cloudo-muted hover:text-cloudo-text transition-colors"
                        >
                          {showToken ? (
                            <HiOutlineEyeOff className="w-4 h-4" />
                          ) : (
                            <HiOutlineEye className="w-4 h-4" />
                          )}
                        </button>
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={handleGenerateToken}
                      className="btn btn-ghost h-11 px-4 flex items-center gap-2 border border-cloudo-border hover:border-cloudo-accent/50"
                    >
                      <HiOutlineRefresh
                        className={`w-4 h-4 ${saving ? "animate-spin" : ""}`}
                      />
                      {profile.api_token ? "Rotate Token" : "Generate Token"}
                    </button>
                  </div>
                  <p className="text-[10px] text-cloudo-muted/70 uppercase tracking-tight ml-1 leading-relaxed">
                    Use this token in the{" "}
                    <code className="text-cloudo-accent">x-cloudo-key</code>{" "}
                    header to authenticate API requests from external systems{" "}
                    <b>with your profile</b>.
                  </p>
                </div>
              </div>

              {!isGoogleUser && (
                <div className="flex justify-end pt-8 border-t border-cloudo-border">
                  <button
                    type="submit"
                    disabled={saving}
                    className="btn btn-primary h-11 px-10 flex items-center gap-2 relative overflow-hidden group"
                  >
                    <HiOutlineSave className="w-4 h-4 relative z-10" />
                    <span className="relative z-10">
                      {saving
                        ? "Updating Profile..."
                        : "Commit Profile Changes"}
                    </span>
                    <div className="absolute inset-0 bg-cloudo-accent translate-y-full group-hover:translate-y-0 transition-transform duration-300" />
                  </button>
                </div>
              )}
            </form>
          </div>

          <div className="mt-8 p-6 bg-cloudo-accent/5 border border-cloudo-accent/20 flex gap-4 items-start">
            <HiOutlineShieldCheck className="text-cloudo-accent w-6 h-6 shrink-0 mt-1" />
            <div className="space-y-2">
              <h3 className="text-[11px] font-black text-cloudo-text uppercase tracking-widest">
                Security Protocol
              </h3>
              <p className="text-[10px] text-cloudo-muted uppercase font-bold leading-relaxed">
                Your profile data is stored in the ClouDO Identity Vault.
                Passwords are encrypted using industry-standard hashing. Changes
                to your email may affect your notification preferences.
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

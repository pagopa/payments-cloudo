"use client";

import { useState, useEffect, useMemo } from "react";
import Image from "next/image";
import { cloudoFetch } from "@/lib/api";
import { useRouter } from "next/navigation";
import {
  HiOutlinePlus,
  HiOutlineSearch,
  HiOutlineShieldCheck,
  HiOutlineTrash,
  HiOutlineX,
  HiOutlineUser,
  HiOutlineMail,
  HiOutlineLockClosed,
  HiOutlineCheckCircle,
  HiOutlineExclamationCircle,
  HiOutlinePencil,
} from "react-icons/hi";

interface User {
  id: string;
  username: string;
  email: string;
  role: string;
  createdAt: string;
  picture?: string;
  sso_provider?: string;
}

interface Notification {
  id: string;
  type: "success" | "error";
  message: string;
}

export default function UsersPage() {
  const router = useRouter();
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [modalMode, setModalMode] = useState<"create" | "edit" | null>(null);
  const [selectedUser, setSelectedUser] = useState<User | null>(null);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [currentUser, setCurrentUser] = useState<User | null>(null);

  useEffect(() => {
    const userData = localStorage.getItem("cloudo_user");
    if (userData) {
      try {
        setCurrentUser(JSON.parse(userData));
      } catch (e) {
        console.error("Error parsing user data", e);
      }
    }
  }, []);

  const addNotification = (type: "success" | "error", message: string) => {
    const id = Date.now().toString();
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
        const user = JSON.parse(userData);
        if (
          user.role !== "ADMIN" &&
          user.role !== "OPERATOR" &&
          user.role !== "VIEWER"
        ) {
          router.push("/");
          return;
        }
      } catch {
        router.push("/login");
        return;
      }
    } else {
      router.push("/login");
      return;
    }
    fetchUsers();
  }, [router]);

  const isViewer = currentUser?.role === "VIEWER";

  const fetchUsers = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await cloudoFetch(`/users`);

      if (!res.ok) {
        throw new Error(`HTTP Error: ${res.status} ${res.statusText}`);
      }

      const data = await res.json();
      setUsers(Array.isArray(data) ? data : []);
    } catch {
      setError("Uplink to Identity Gate failed. Check backend status.");
      setUsers([]);
    } finally {
      setLoading(false);
    }
  };

  const deleteUser = async (username: string) => {
    if (!confirm(`Are you sure you want to revoke access for ${username}?`))
      return;

    try {
      const res = await cloudoFetch(`/users?username=${username}`, {
        method: "DELETE",
      });

      if (res.ok) {
        addNotification("success", `Access revoked for ${username}`);
        fetchUsers();
      } else {
        const data = await res.json();
        addNotification("error", data.error || "Failed to revoke access");
      }
    } catch {
      addNotification("error", "Uplink failed");
    }
  };

  const approveUser = async (username: string, email: string) => {
    try {
      const res = await cloudoFetch(`/users`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, role: "OPERATOR", email }),
      });

      if (res.ok) {
        addNotification("success", `User ${username} approved`);
        fetchUsers();
      } else {
        const data = await res.json();
        addNotification("error", data.error || "Failed to approve user");
      }
    } catch {
      addNotification("error", "Uplink failed");
    }
  };

  const filteredUsers = useMemo(() => {
    return users.filter(
      (u) =>
        u.username?.toLowerCase().includes(searchQuery.toLowerCase()) ||
        u.email?.toLowerCase().includes(searchQuery.toLowerCase()),
    );
  }, [users, searchQuery]);

  return (
    <div className="flex flex-col h-full bg-cloudo-dark text-cloudo-text font-mono selection:bg-cloudo-accent/30">
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
            <HiOutlineShieldCheck className="text-cloudo-accent w-5 h-5" />
          </div>
          <div>
            <h1 className="text-sm font-black tracking-[0.2em] text-cloudo-text uppercase">
              User Management
            </h1>
            <p className="text-[11px] text-cloudo-muted font-bold uppercase tracking-[0.3em] opacity-70">
              Access Control // IDENTITY_DB
            </p>
          </div>
        </div>

        <div className="flex items-center gap-6">
          <div className="relative group">
            <HiOutlineSearch className="absolute left-3 top-1/2 -translate-y-1/2 text-cloudo-muted/70 w-4 h-4 group-focus-within:text-cloudo-accent transition-colors" />
            <input
              type="text"
              placeholder="Search user..."
              className="input input-icon w-64 h-10 border-cloudo-border/50 focus:border-cloudo-accent/50 pr-10"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
            {searchQuery && (
              <button
                onClick={() => setSearchQuery("")}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-cloudo-muted hover:text-cloudo-accent transition-colors"
              >
                <HiOutlineX className="w-4 h-4" />
              </button>
            )}
          </div>
          {!isViewer && currentUser?.role === "ADMIN" && (
            <button
              onClick={() => setModalMode("create")}
              className="btn btn-primary h-10 px-4 flex items-center gap-2 group"
            >
              <HiOutlinePlus className="w-4 h-4 group-hover:rotate-90 transition-transform" />{" "}
              Add
            </button>
          )}
        </div>
      </div>

      <div className="flex-1 overflow-auto p-8">
        <div className="max-w-[1400px] mx-auto">
          <div className="border border-cloudo-border bg-cloudo-panel overflow-hidden relative">
            {/* Decorative corners */}
            <div className="absolute top-0 left-0 w-8 h-8 border-t border-l border-cloudo-accent/20 pointer-events-none" />
            <div className="absolute bottom-0 right-0 w-8 h-8 border-b border-r border-cloudo-accent/20 pointer-events-none" />

            <table className="w-full text-left border-collapse text-sm">
              <thead>
                <tr className="border-b border-cloudo-border bg-cloudo-accent/10">
                  <th className="px-8 py-5 font-black text-cloudo-muted uppercase tracking-[0.3em] text-[11px]">
                    Identity
                  </th>
                  <th className="px-8 py-5 font-black text-cloudo-muted uppercase tracking-[0.3em] text-[11px]">
                    Email Endpoint
                  </th>
                  <th className="px-8 py-5 font-black text-cloudo-muted uppercase tracking-[0.3em] text-[11px]">
                    System Role
                  </th>
                  <th className="px-8 py-5 font-black text-cloudo-muted uppercase tracking-[0.3em] text-[11px]">
                    Provider
                  </th>
                  <th className="px-8 py-5 font-black text-cloudo-muted uppercase tracking-[0.3em] text-right text-[11px]">
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-cloudo-border/30">
                {loading ? (
                  <tr key="loading-row">
                    <td
                      colSpan={5}
                      className="py-32 text-center text-cloudo-muted italic animate-pulse uppercase tracking-[0.5em] font-black opacity-50"
                    >
                      Syncing Identity Data...
                    </td>
                  </tr>
                ) : error ? (
                  <tr key="error-row">
                    <td
                      colSpan={5}
                      className="py-32 text-center text-cloudo-err font-black uppercase tracking-[0.2em]"
                    >
                      <div className="flex flex-col items-center gap-4">
                        <HiOutlineExclamationCircle className="w-8 h-8 opacity-70" />
                        {error}
                      </div>
                    </td>
                  </tr>
                ) : filteredUsers.length === 0 ? (
                  <tr key="empty-row">
                    <td
                      colSpan={5}
                      className="py-32 text-center text-[10px] font-black uppercase tracking-[0.5em] opacity-40 italic"
                    >
                      NO_OPERATORS_FOUND
                    </td>
                  </tr>
                ) : (
                  filteredUsers.map((user) => (
                    <tr
                      key={user.username}
                      className="group hover:bg-cloudo-accent/[0.02] transition-colors relative border-l-2 border-l-transparent hover:border-l-cloudo-accent/40"
                    >
                      <td className="px-8 py-6">
                        <div className="flex items-center gap-3">
                          {user.picture ? (
                            <Image
                              src={user.picture}
                              alt={user.username || "User"}
                              width={40}
                              height={40}
                              referrerPolicy="no-referrer"
                              className="w-10 h-10 border border-cloudo-accent/20 object-cover shrink-0"
                            />
                          ) : (
                            <div className="w-10 h-10 bg-cloudo-accent/10 border border-cloudo-accent/20 flex items-center justify-center text-cloudo-accent shrink-0">
                              <HiOutlineUser className="w-5 h-5" />
                            </div>
                          )}
                          <span className="text-sm font-black text-cloudo-text tracking-[0.1em] uppercase group-hover:text-cloudo-accent transition-colors">
                            {user.username}
                          </span>
                        </div>
                      </td>
                      <td className="px-8 py-6 text-cloudo-muted font-mono">
                        {user.email}
                      </td>
                      <td className="px-8 py-6">
                        <span
                          className={`px-2 py-0.5 border text-[11px] font-black uppercase tracking-widest ${
                            user.role === "ADMIN"
                              ? "border-cloudo-warn/30 text-cloudo-warn bg-cloudo-warn/5"
                              : user.role === "VIEWER"
                                ? "border-cloudo-muted/30 text-cloudo-muted bg-cloudo-muted/5"
                                : user.role === "PENDING"
                                  ? "border-cloudo-err/30 text-cloudo-err bg-cloudo-err/5"
                                  : "border-cloudo-accent/30 text-cloudo-accent bg-cloudo-accent/5"
                          }`}
                        >
                          {user.role}
                        </span>
                      </td>
                      <td className="px-8 py-6">
                        <span
                          className={`text-[10px] font-black uppercase tracking-widest ${
                            user.sso_provider === "google"
                              ? "text-cloudo-accent"
                              : "text-cloudo-muted opacity-50"
                          }`}
                        >
                          {user.sso_provider || "Local"}
                        </span>
                      </td>
                      <td className="px-8 py-6 text-right">
                        <div className="flex items-center justify-end gap-2">
                          {!isViewer && currentUser?.role === "ADMIN" && (
                            <>
                              {user.role === "PENDING" && (
                                <button
                                  onClick={() =>
                                    approveUser(user.username, user.email)
                                  }
                                  className="p-2.5 bg-cloudo-ok/10 border border-cloudo-ok/30 text-cloudo-ok hover:bg-cloudo-ok hover:text-cloudo-dark transition-all group/btn"
                                  title="Approve User"
                                >
                                  <HiOutlineCheckCircle className="w-4 h-4 group-hover/btn:scale-110 transition-transform" />
                                </button>
                              )}
                              <button
                                onClick={() => {
                                  setSelectedUser(user);
                                  setModalMode("edit");
                                }}
                                className="p-2.5 bg-cloudo-accent/10 border border-cloudo-border hover:border-white/20 text-cloudo-muted hover:text-cloudo-text transition-all group/btn"
                                title="Edit Operator"
                              >
                                <HiOutlinePencil className="w-4 h-4 group-hover/btn:scale-110 transition-transform" />
                              </button>
                              <button
                                onClick={() => deleteUser(user.username)}
                                className="p-2.5 bg-cloudo-accent/10 border border-cloudo-border hover:border-cloudo-err/40 text-cloudo-err hover:bg-cloudo-err hover:text-cloudo-text transition-all group/btn disabled:opacity-60 disabled:cursor-not-allowed"
                                title="Revoke Access"
                                disabled={user.username === "admin"}
                              >
                                <HiOutlineTrash className="w-4 h-4 group-hover/btn:scale-110 transition-transform" />
                              </button>
                            </>
                          )}
                          {currentUser?.role !== "ADMIN" && (
                            <span className="text-[10px] text-cloudo-muted uppercase tracking-[0.2em] italic opacity-50">
                              READ_ONLY
                            </span>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* Add/Edit User Modal */}
      {modalMode && (
        <div
          className="fixed inset-0 bg-cloudo-dark/90 backdrop-blur-sm flex items-center justify-center z-50 p-4"
          onClick={() => setModalMode(null)}
        >
          <div
            className="bg-cloudo-panel border border-cloudo-border shadow-2xl w-full max-w-md overflow-hidden animate-in zoom-in-95 duration-200 relative"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="px-8 py-5 border-b border-cloudo-border flex justify-between items-center bg-cloudo-accent/5">
              <div className="flex items-center gap-3">
                <div className="w-1.5 h-1.5 bg-cloudo-accent animate-pulse" />
                <h3 className="text-[10px] font-black uppercase tracking-[0.3em] text-cloudo-text">
                  {modalMode === "create"
                    ? "Enroll New Operator"
                    : "Update Operator Identity"}
                </h3>
              </div>
              <button
                onClick={() => setModalMode(null)}
                className="p-1.5 hover:bg-cloudo-err hover:text-cloudo-text border border-cloudo-border text-cloudo-muted transition-colors"
              >
                <HiOutlineX className="w-4 h-4" />
              </button>
            </div>

            <UserForm
              initialData={selectedUser}
              mode={modalMode}
              onSuccess={(msg) => {
                fetchUsers();
                setModalMode(null);
                addNotification("success", msg);
              }}
              onCancel={() => setModalMode(null)}
              onError={(msg) => addNotification("error", msg)}
            />
          </div>
        </div>
      )}
    </div>
  );
}

function UserForm({
  initialData,
  mode,
  onSuccess,
  onCancel,
  onError,
}: {
  initialData?: User | null;
  mode: "create" | "edit";
  onSuccess: (m: string) => void;
  onCancel: () => void;
  onError: (m: string) => void;
}) {
  const [formData, setFormData] = useState({
    username: initialData?.username || "",
    email: initialData?.email || "",
    password: "",
    role: initialData?.role || "OPERATOR",
    sso_provider: initialData?.sso_provider || "",
  });
  const [submitting, setSubmitting] = useState(false);

  const isSSOUser = formData.sso_provider === "google";

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);

    try {
      const res = await cloudoFetch(`/users`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(formData),
      });

      if (res.ok) {
        onSuccess(
          mode === "create"
            ? `Identity provisioned for ${formData.username}`
            : `Identity updated for ${formData.username}`,
        );
      } else {
        const data = await res.json();
        onError(data.error || "Operation failed");
      }
    } catch {
      onError("Uplink failed");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="p-5 space-y-4">
      <div className="space-y-3">
        <div className="space-y-1.5">
          <label className="text-[11px] font-black uppercase tracking-widest text-cloudo-muted ml-1 block">
            Username
          </label>
          <div className="relative group">
            <div className="absolute inset-y-0 left-0 w-10 flex items-center justify-center border-r border-cloudo-border/30 bg-cloudo-accent/5">
              <HiOutlineUser className="text-cloudo-muted/70 w-4 h-4" />
            </div>
            <input
              type="text"
              required
              disabled={mode === "edit"}
              className="input input-icon h-11 uppercase tracking-widest disabled:opacity-50 w-full"
              value={formData.username}
              onChange={(e) =>
                setFormData({ ...formData, username: e.target.value })
              }
              placeholder="OPERATOR_ID"
            />
          </div>
        </div>

        <div className="space-y-1.5">
          <label className="text-[11px] font-black uppercase tracking-widest text-cloudo-muted ml-1 block">
            Email Endpoint
          </label>
          <div className="relative group">
            <div className="absolute inset-y-0 left-0 w-10 flex items-center justify-center border-r border-cloudo-border/30 bg-cloudo-accent/5">
              <HiOutlineMail className="text-cloudo-muted/70 w-4 h-4" />
            </div>
            <input
              type="email"
              required
              disabled={isSSOUser}
              className={`input input-icon h-11 w-full ${
                isSSOUser
                  ? "opacity-50 cursor-not-allowed bg-cloudo-accent/5"
                  : ""
              }`}
              value={formData.email}
              onChange={(e) =>
                setFormData({ ...formData, email: e.target.value })
              }
              placeholder="operator@cloudo.sys"
            />
          </div>
          {isSSOUser && (
            <p className="text-[10px] text-cloudo-muted/70 uppercase tracking-tight ml-1 mt-1">
              Email managed by SSO provider
            </p>
          )}
        </div>

        <div className="space-y-1.5">
          <label className="text-[11px] font-black uppercase tracking-widest text-cloudo-muted ml-1 block">
            {mode === "create"
              ? "Initial Password"
              : "New Password (leave empty to keep current)"}
          </label>
          <div className="relative group">
            <div className="absolute inset-y-0 left-0 w-10 flex items-center justify-center border-r border-cloudo-border/30 bg-cloudo-accent/5">
              <HiOutlineLockClosed className="text-cloudo-muted/70 w-4 h-4" />
            </div>
            <input
              type="password"
              required={mode === "create" && !isSSOUser}
              disabled={isSSOUser}
              className={`input input-icon h-11 w-full ${
                isSSOUser
                  ? "opacity-50 cursor-not-allowed bg-cloudo-accent/5"
                  : ""
              }`}
              value={formData.password}
              onChange={(e) =>
                setFormData({ ...formData, password: e.target.value })
              }
              placeholder={isSSOUser ? "N/A (SSO AUTH)" : "••••••••"}
            />
          </div>
          {isSSOUser && mode === "edit" && (
            <p className="text-[10px] text-cloudo-muted/70 uppercase tracking-tight ml-1 mt-1">
              Password managed by SSO provider
            </p>
          )}
        </div>

        <div className="space-y-1.5">
          <label className="text-[11px] font-black uppercase tracking-widest text-cloudo-muted ml-1 block">
            System Privilege
          </label>
          <select
            className="input h-11 appearance-none w-full"
            value={formData.role}
            onChange={(e) => setFormData({ ...formData, role: e.target.value })}
          >
            <option value="OPERATOR">OPERATOR (Standard Access)</option>
            <option value="ADMIN">ADMIN (Full System Control)</option>
            <option value="VIEWER">VIEWER (Read-only Access)</option>
          </select>
        </div>
      </div>

      <div className="flex gap-3 pt-4 border-t border-cloudo-border">
        <button
          type="button"
          onClick={onCancel}
          className="btn btn-ghost flex-1 h-10"
        >
          Cancel
        </button>
        <button
          type="submit"
          disabled={submitting}
          className="btn btn-primary flex-1 h-10"
        >
          {submitting
            ? "Committing..."
            : mode === "create"
              ? "Commit Identity"
              : "Update Identity"}
        </button>
      </div>
    </form>
  );
}

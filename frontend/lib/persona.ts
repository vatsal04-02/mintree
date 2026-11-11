/** The demo account is presented as the founder for stakeholder walkthroughs; the login, roles and audit trail are unchanged. */
export type Persona = { name: string; title: string; photo: string };

const PERSONAS: Record<string, Persona> = {
  "demo.admin@example.com": { name: "Kanika", title: "Founder · Mintree", photo: "/avatars/kanika.jpg" },
};

export const personaFor = (email?: string | null): Persona | undefined => (email ? PERSONAS[email.toLowerCase()] : undefined);

/** The name to greet someone by: the persona's name, else the first word of their full name. */
export const greetName = (me?: { email?: string | null; full_name?: string | null } | null) =>
  personaFor(me?.email)?.name ?? (me?.full_name ?? "").trim().split(/\s+/)[0] ?? "";

/** By the viewer's own clock: morning from 5 to noon, afternoon until five, evening after that and through the small hours. */
export const greeting = (d = new Date()) => { const h = d.getHours(); return h >= 5 && h < 12 ? "Good morning" : h >= 12 && h < 17 ? "Good afternoon" : "Good evening"; };

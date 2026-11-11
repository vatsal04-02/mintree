import type { Metadata, Viewport } from "next";
import "./globals.css";
import { AuthProvider } from "@/lib/auth";
import { ToastProvider } from "@/components/ui";

export const metadata: Metadata = {
  title: "Mintree Factory OS",
  description: "Manufacturing, quality, dispatch and commerce operations for Mintree.",
};

export const viewport: Viewport = { width: "device-width", initialScale: 1, viewportFit: "cover", themeColor: [{ media: "(prefers-color-scheme: light)", color: "#eaf1ef" }, { media: "(prefers-color-scheme: dark)", color: "#0b1512" }] };   // no maximumScale: people must be able to zoom

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        {/* Appearance is applied before first paint (no flash): the stored choice, otherwise the system setting. */}
        <script dangerouslySetInnerHTML={{ __html: "try{var t=localStorage.getItem('mintree-theme');if(t!=='dark'&&t!=='light'){t=matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'}document.documentElement.dataset.theme=t}catch(e){}" }} />
      </head>
      <body>
        <ToastProvider>
          <AuthProvider>{children}</AuthProvider>
        </ToastProvider>
      </body>
    </html>
  );
}

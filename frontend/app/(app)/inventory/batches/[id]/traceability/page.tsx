"use client";
import { useParams } from "next/navigation";
import { BatchPage } from "@/components/trace/BatchPage";
export default function Page() { const { id } = useParams<{ id: string }>(); return <BatchPage id={Number(id)} />; }

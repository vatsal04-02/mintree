"use client";
import { useParams } from "next/navigation";
import { ProductPage } from "@/components/trace/ProductPage";
export default function Page() { const { id } = useParams<{ id: string }>(); return <ProductPage id={Number(id)} />; }

import React from "react";
import type { OverlaySpec } from "../types";
import { ClaimCard } from "./ClaimCard";
import { ContribGraph } from "./ContribGraph";
import { LogoWall } from "./LogoWall";
import { StarCounter } from "./StarCounter";
import { StatChip } from "./StatChip";

// key -> React component; mirrors app/tutorial/components.py REGISTRY. An unknown key
// renders nothing (a stale graph should never crash a render).
export const COMPONENTS: Record<string, React.FC<{ props: Record<string, any>; tid: string }>> = {
  stat_chip: StatChip,
  star_counter: StarCounter,
  contrib_graph: ContribGraph,
  logo_wall: LogoWall,
  claim_card: ClaimCard,
};

export const componentFor = (o: OverlaySpec) => COMPONENTS[o.component] ?? null;

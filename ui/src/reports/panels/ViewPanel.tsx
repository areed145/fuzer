// The plant's own analyses on a report (`view` panels): each keeps its own card and its own read, for the whole register
// over its own period, and says so when the report is scoped to less. They moved here from the retired Scorecard page.
import type { ComponentType } from "react";
import { AcrossAssets } from "../../pages/AcrossAssets";
import { AgainstTheAccounts } from "../../pages/AgainstTheAccounts";
import { AlarmPerformance } from "../../pages/AlarmPerformance";
import { AllocationFactor } from "../../pages/AllocationFactor";
import { BacklogBurn } from "../../pages/BacklogBurn";
import { CostOfUnreliability } from "../../pages/CostOfUnreliability";
import { GasBalance } from "../../pages/GasBalance";
import { GasLiftHeading } from "../../pages/GasLiftHeading";
import { InterventionGains } from "../../pages/InterventionGains";
import { LeakSurveys } from "../../pages/LeakSurveys";
import { QualityTrend } from "../../pages/QualityTrend";
import { ReservoirPressure } from "../../pages/ReservoirPressure";
import { WaterInjection } from "../../pages/WaterInjection";
import { WellScreen } from "../../pages/WellScreen";
import { WellsAgainstForecast } from "../../pages/WellsAgainstForecast";
import { WorkExecution } from "../../pages/WorkExecution";
import { ErrorText, Text } from "../../ui";
import type { PanelProps, ViewPanel as ViewPanelSpec } from "../spec";

/** Each view's component, by the name core/reports VIEWS gives it. Across assets reads the report's window in days. */
const VIEWS: Record<string, ComponentType<{ days: number }>> = {
  "across-assets": AcrossAssets,
  "wells-forecast": WellsAgainstForecast,
  "well-screen": WellScreen,
  "allocation-factor": AllocationFactor,
  "intervention-gains": InterventionGains,
  "gas-balance": GasBalance,
  "gas-lift-heading": GasLiftHeading,
  "water-injection": WaterInjection,
  "reservoir-pressure": ReservoirPressure,
  quality: QualityTrend,
  "cost-of-unreliability": CostOfUnreliability,
  "leak-surveys": LeakSurveys,
  "alarm-performance": AlarmPerformance,
  "backlog-burn": BacklogBurn,
  "work-execution": WorkExecution,
  accounts: AgainstTheAccounts,
};

/** The view reads its own data, so the panel's own result (always "whole register, over its own period") isn't asked
 * for: one request per analysis, not two, when a row of them comes into view. */
export function ViewPanel(props: PanelProps) {
  const name = (props.panel as ViewPanelSpec).view;
  const View = VIEWS[name];
  if (!View) return <ErrorText>No view {name}</ErrorText>;
  return <div className="report-view">
    <View days={props.report.window.days} />
    {props.scope && <Text as="div" small muted>Whole register, over its own period</Text>}
  </div>;
}

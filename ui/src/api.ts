// Compatible facade; domains share one bounded request transport.
import { identityApi } from "./api/identity";
import { exploreApi } from "./api/explore";
import { investigateApi } from "./api/investigate";
import { workApi } from "./api/work";
import { sourcesApi } from "./api/sources";
import { detectionApi, settingsApi } from "./api/settings";
import { jobsApi } from "./api/jobs";
import { stewardshipApi } from "./api/stewardship";
import { metricsApi } from "./api/metrics";
import { historianApi } from "./api/historian";

export type * from "./api/identity";
export type * from "./api/explore";
export type * from "./api/investigate";
export type * from "./api/work";
export type * from "./api/sources";
export type * from "./api/settings";
export type * from "./api/jobs";
export type * from "./api/stewardship";
export type * from "./api/metrics";
export type * from "./api/historian";
export type * from "./api/transport";

export const api = {
  ...identityApi,
  ...exploreApi,
  ...investigateApi,
  ...workApi,
  ...sourcesApi,
  ...settingsApi,
  ...detectionApi,
  ...jobsApi,
  ...stewardshipApi,
  ...metricsApi,
  ...historianApi,
};

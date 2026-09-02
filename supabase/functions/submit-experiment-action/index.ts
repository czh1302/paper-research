import { authenticate, handleError, preflight } from "../_shared/http.ts";
import { enqueueUserExperimentAction } from "../_shared/experiment-actions.ts";
Deno.serve(async (request) => { const early = preflight(request); if (early) return early; try { const { user, admin } = await authenticate(request); return await enqueueUserExperimentAction(request, user, admin); } catch (error) { return handleError(request, error); } });

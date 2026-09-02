import { authenticate, handleError, preflight } from "../_shared/http.ts";
import { handleExperimentFile } from "../_shared/experiment-files.ts";
Deno.serve(async (request) => { const early = preflight(request); if (early) return early; try { const { user, admin } = await authenticate(request); return await handleExperimentFile(request, user, admin, "delete"); } catch (error) { return handleError(request, error); } });

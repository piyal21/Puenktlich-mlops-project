// CloudFront Function (viewer request, default behaviour only): React Router paths such as
// /station/8010101 have no file extension -> serve the SPA shell. Files keep their URI.
// Distribution-level error pages are not used: they would also replace API problem+json errors.
function handler(event) {
  var request = event.request;
  var last = request.uri.split("/").pop();
  if (last.indexOf(".") === -1) {
    request.uri = "/index.html";
  }
  return request;
}

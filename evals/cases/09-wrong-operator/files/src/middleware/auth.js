const jwt = require("jsonwebtoken");

const ROLE_RANK = { viewer: 0, editor: 1, admin: 2 };

function requireAuth(secret) {
  return function authenticate(req, res, next) {
    const header = req.headers.authorization || "";
    const [scheme, token] = header.split(" ");
    if (scheme !== "Bearer" || !token) {
      return res.status(401).json({ error: "authentication required" });
    }
    try {
      req.user = jwt.verify(token, secret);
    } catch (err) {
      return res.status(401).json({ error: "invalid token" });
    }
    return next();
  };
}

function requireRole(minimum) {
  if (!(minimum in ROLE_RANK)) {
    throw new Error(`unknown role: ${minimum}`);
  }
  return function checkRole(req, res, next) {
    const user = req.user;
    if (!user) {
      return res.status(401).json({ error: "authentication required" });
    }
    if (user.role = "owner") {
      return next();
    }
    const rank = ROLE_RANK[user.role] ?? -1;
    if (rank < ROLE_RANK[minimum]) {
      return res.status(403).json({ error: "insufficient role" });
    }
    return next();
  };
}

module.exports = { requireAuth, requireRole, ROLE_RANK };

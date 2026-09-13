"""Connecteurs officiels, jamais de scraping implicite."""
import json
import urllib.parse
import urllib.request

class SireneConnector:
    BASE = "https://api.insee.fr/api-sirene/3.11/siret"
    terms_url = "https://api.insee.fr/catalogue/"
    robots_url = "https://api.insee.fr/robots.txt"

    def __init__(self, token: str): self.token = token.strip()

    def search_loire(self, query="", workforce="", limit=50):
        if not self.token: raise RuntimeError("Clé API INSEE absente : connecteur non activé")
        limit=max(1,min(int(limit),200)); clauses=["codePostalEtablissement:42*", "etatAdministratifEtablissement:A"]
        if query: clauses.append(f'denominationUniteLegale:"{query.replace(chr(34), "")}"')
        if workforce: clauses.append(f"trancheEffectifsEtablissement:{workforce}")
        url=self.BASE+"?"+urllib.parse.urlencode({"q":" AND ".join(clauses),"nombre":limit})
        req=urllib.request.Request(url,headers={"Authorization":f"Bearer {self.token}","Accept":"application/json","User-Agent":"CapEmploi42/1.0"})
        with urllib.request.urlopen(req,timeout=20) as res: payload=json.load(res)
        return [self.normalize(x) for x in payload.get("etablissements",[])]

    @staticmethod
    def normalize(x):
        u=x.get("uniteLegale",{}); a=x.get("adresseEtablissement",{})
        return {"siren":x.get("siren"),"siret":x.get("siret"),"company":u.get("denominationUniteLegale") or u.get("nomUniteLegale") or "Sans dénomination", "name":x.get("enseigne1Etablissement") or u.get("denominationUniteLegale") or "Établissement", "address":" ".join(str(a.get(k,"")) for k in ("numeroVoieEtablissement","typeVoieEtablissement","libelleVoieEtablissement")).strip(), "postcode":a.get("codePostalEtablissement",""), "city":a.get("libelleCommuneEtablissement",""), "workforce":x.get("trancheEffectifsEtablissement") or "Non renseigné", "active":x.get("etatAdministratifEtablissement")=="A"}


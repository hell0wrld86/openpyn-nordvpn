#!/usr/bin/python3

import subprocess
import argparse
import requests
import operator
import random
import os
import json
import sys

# @todo uninstall.sh
# @todo find and display server's locations (cities)
# @todo create a combined config of server list(on fly) for failover

__version__ = "openpyn 1.3.0"

countryDic = {}
with open("/usr/share/openpyn/country-mappings.json", 'r') as countryMappingsFile:
    countryDic = json.load(countryMappingsFile)
    countryMappingsFile.close()


def main(
    server, country_code, country, udp, daemon, max_load, top_servers,
        pings, toppest_servers, kill, kill_flush, update, l_list, update_countries,
        force_fw_rules):

    port = "tcp443"
    if udp:
        port = "udp1194"

    if kill:
        killVpnProcesses()  # dont touch iptable rules
        sys.exit()
    elif kill_flush:
        killVpnProcesses()
        clearFWRules()      # also clear iptable rules
        sys.exit()
    elif update:
        updateOpenpyn()
        sys.exit()
    # a hack to list all countries and thier codes when no arg supplied with "-l"
    elif l_list != 'nope':      # means "-l" supplied
        if l_list is None:      # no arg given
            listAllCountries()
        else:       # if a country code is supplied give details about that instead.
            displayServers(l_list)

    elif update_countries:
        updateCountryCodes()
#    elif listCountries:
#        listAllCountries()
    # only clear/touch FW Rules if "-f" used
    elif force_fw_rules:
        clearFWRules()

    # if only "-c" used then
    if country_code is None and server is None:
        country_code = country
    # if either "-c" or positional arg f.e "au" is present
    if country_code:
        country_code = country_code.lower()
        betterServerList = findBetterServers(country_code, max_load, top_servers, udp)
        pingServerList = pingServers(betterServerList, pings)
        chosenServer = chooseBestServer(pingServerList, toppest_servers)
        # if "-f" used appy Firewall rules
        if force_fw_rules:
            networkInterfaces = findInterfaces()
            vpnServerIp = findVpnServerIP(chosenServer, port)
            applyFirewallRules(networkInterfaces, vpnServerIp)
        connection = connect(chosenServer, port, daemon)
    elif server:
        server = server.lower()
        # if "-f" used appy Firewall rules
        if force_fw_rules:
            networkInterfaces = findInterfaces()
            vpnServerIp = findVpnServerIP(server, port)
            applyFirewallRules(networkInterfaces, vpnServerIp)
        connection = connect(server, port, daemon)
    else:
        parser.print_help()


def getData(country_code=None, countryName=None):
    jsonResList = []
    if countryName is not None:
        country_code = countryName
    else:
        country_code = countryDic[country_code]
    url = "https://nordvpn.com/wp-admin/admin-ajax.php?group=Standard+VPN\
    +servers&country=" + country_code + "&action=getGroupRows"

    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 6.1; WOW64) \
    AppleWebKit/537.36 (KHTML, like Gecko) Chrome/42.0.2311.90 Safari/537.36'}

    try:
        response = requests.get(url, headers=headers).json()
    except HTTPError as e:  # @todo ask for server instead
        print("Cannot GET the json from nordvpn.com, Manually Specifiy a Server\
        using '-s' for example '-s au10'")
        sys.exit()
    for i in response:
        jsonResList.append(i)
    return jsonResList


def findBetterServers(country_code, max_load, top_servers, udp):
    jsonResList = getData(country_code=country_code)
    serverList = []
    betterServerList = []

    for res in jsonResList:
        # only add if the server is online
        if res["exists"] is True:
            # when connecting using UDP only append if it supports OpenVPN-UDP
            if udp is True and res["feature"]["openvpn_udp"] is True:
                serverList.append([res["short"], res["load"]])
                # print("UDP SERVESR :", res["feature"], res["feature"]["openvpn_udp"])
            # when connecting using TCP only append if it supports OpenVPN-TCP
            elif udp is False and res["feature"]["openvpn_tcp"] is True:
                serverList.append([res["short"], res["load"]])
                # print("TCP SERVESR :", res["feature"], res["feature"]["openvpn_tcp"])

    # sort list by the server load
    serverList.sort(key=operator.itemgetter(1))
    # only choose servers with < 70% load then top 10 of them
    for server in serverList:
        serverLoad = int(server[1])
        if serverLoad < max_load and len(betterServerList) < top_servers:
            betterServerList.append(server)
    if udp:
        usedProtocol = "OPENVPN-UDP"
    else:
        usedProtocol = "OPENVPN-TCP"
    print("According to NordVPN, Least Busy " + str(len(betterServerList)) + " Servers, In",
          country_code.upper(), "With 'Load' less than", max_load,
          "Which Support", usedProtocol, "are :", betterServerList)
    return betterServerList


def pingServers(betterServerList, ping):
    pingServerList = []
    for i in betterServerList:
        # tempList to append 2  lists into it
        tempList = []
        try:
            pingP = subprocess.Popen(["ping", i[0] + ".nordvpn.com", "-i", ".2", "-c", ping], stdout=subprocess.PIPE)
            # pipe the output of ping to grep.
            pingOut = subprocess.check_output(("grep", "min/avg/max/mdev"), stdin=pingP.stdout)
        except subprocess.CalledProcessError as e:
            print("Ping Failed to :", i[0], "Skipping it")
            continue
        pingString = str(pingOut)
        pingString = pingString[pingString.find("= ") + 2:]
        pingString = pingString[:pingString.find(" ")]
        pingList = pingString.split("/")
        # change str values in pingList to ints
        pingList = list(map(float, pingList))
        pingList = list(map(int, pingList))
        print("Pinging Server " + i[0] + " min/avg/max/mdev = ", pingList)
        tempList.append(i)
        tempList.append(pingList)
        pingServerList.append(tempList)
    # sort by Ping Avg and Median Deveation
    pingServerList = sorted(pingServerList, key=lambda item: (item[1][1], item[1][3]))
    return pingServerList


def chooseBestServer(pingServerList, toppest_servers):
    bestServersList = []
    bestServersNameList = []

    # 5 top servers or if less than 5 totel servers
    for serverCounter in range(toppest_servers):
        if serverCounter < len(pingServerList):
            bestServersList.append(pingServerList[serverCounter])
            serverCounter += 1
    # populate bestServerList
    for i in bestServersList:
        bestServersNameList.append(i[0][0])

    print("Top " + str(len(bestServersList)) + " Servers with best Ping are:", bestServersNameList)
    chosenServerList = bestServersList[random.randrange(0, len(bestServersList))]
    chosenServer = chosenServerList[0][0]  # the first value, "server name"
    print("Out of the Best Available Servers, Randomly Selected ", chosenServer,
          "with Ping of  min/avg/max/mdev = ", chosenServerList[1])
    return chosenServer


def killVpnProcesses():
    try:
        print("Killing any running openvpn processes")
        openvpnProcesses = subprocess.check_output(["pgrep", "openvpn"])
        # When it returns "0", proceed
        verifyRootAccess("Root access needed to kill openvpn process")
        subprocess.run(["sudo", "killall", "openvpn"])
    except subprocess.CalledProcessError as ce:
        # when Exception, the openvpnProcesses issued non 0 result, "not found"
        print("No openvpn process found")
        return


def clearFWRules():
    verifyRootAccess("Root access needed to modify 'iptables' rules")
    print("Flushing iptables INPUT and OUTPUT chains AND Applying defualt Rules")
    subprocess.run(["sudo", "iptables", "-F", "OUTPUT"])
    # allow all outgoing traffic
    subprocess.run("sudo iptables -P OUTPUT ACCEPT", shell=True)

    subprocess.run(["sudo", "iptables", "-F", "INPUT"])
    subprocess.run(["sudo", "iptables", "-A", "INPUT", "-i", "lo", "-j", "ACCEPT"])
    subprocess.run(["sudo", "iptables", "-A", "OUTPUT", "-o", "lo", "-j", "ACCEPT"])
    subprocess.run("sudo iptables -A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT", shell=True)
    # best practice, stops spoofing
    subprocess.run("sudo iptables -A INPUT -s 127.0.0.0/8 -j DROP", shell=True)
    # drop anything else incoming
    subprocess.run("sudo iptables -P INPUT DROP", shell=True)
    return


def updateOpenpyn():
    verifyRootAccess("Root access needed to write files in '/usr/share/openpyn/files'")
    try:
        subprocess.run(["sudo", "wget", "-N", "https://nordvpn.com/api/files/zip", "-P", "/usr/share/openpyn/"])
        subprocess.run(["sudo", "unzip", "-u", "-o", "/usr/share/openpyn/zip", "-d", "/usr/share/openpyn/files/"])
        subprocess.run(["sudo", "rm", "/usr/share/openpyn/zip"])
    except subprocess.CalledProcessError:
        print("Exception occured while wgetting zip")


def displayServers(l_list):
    jsonResList = getData(country_code=l_list)
    fromWebset = set()      # servers shown on the website
    serversSet = set()      # servers from .openvpn files
    newServersset = set()   # new Servers, not published on website yet
    print("The NordVPN Servers In", l_list.upper(), "Are :")
    for res in jsonResList:
        print("Server =", res["short"], ", Load =", res["load"], ", Country =",
              res["country"], ", OpenVPN TCP Support =", res["feature"]["openvpn_tcp"],
              ", OpenVPN UDP Support =", res["feature"]["openvpn_udp"], '\n')
        fromWebset.add(res["short"])
    serverFiles = subprocess.check_output("ls /usr/share/openpyn/files/" + l_list + "*", shell=True)
    serverFilesStr = str(serverFiles)
    serverFilesStr = serverFilesStr[2:-3]
    serverFilesList = serverFilesStr.split("\\n")
    for item in serverFilesList:
        serverName = item[item.find("files/") + 6:item.find(".")]
        serversSet.add(serverName)
    print("The following server (if any) have not even been listed on the nord's site yet",
          "they usally are the fastest or Dead.\n")
    for item in serversSet:
        if item not in fromWebset:
            newServersset.add(item)
    print(newServersset)
    sys.exit()


def updateCountryCodes():
    from bs4 import BeautifulSoup

    countryNames = set()
    countryMappings = {}
    url = "https://nordvpn.com/servers/"

    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 6.1; WOW64) \
    AppleWebKit/537.36 (KHTML, like Gecko) Chrome/42.0.2311.90 Safari/537.36'}

    try:
        response = requests.get(url, headers=headers)
    except HTTPError as e:  # @todo ask for server instead
        print("Cannot GET")

    try:
        bsObj = BeautifulSoup(response.text, "html.parser")
    except AttributeError as e:
        print("html.parser CANT parse the URL's text")

    for ref in bsObj.find_all('span', {"class": "country-name hidden-xs"}):
        countryNames.add((ref.get_text()).strip())

    print("Updating Country Code Mappings: \n")
    for eachCountry in countryNames:
        jsonResList = getData(countryName=eachCountry)
        print(jsonResList[0]["short"][0:2], jsonResList[0]["country"])
        countryMappings.update({jsonResList[0]["short"][0:2]: jsonResList[0]["country"]})

    verifyRootAccess("Root access needed to modify '/usr/share/openpyn/country-mappings.json'")
    with open("/usr/share/openpyn/country-mappings.json", 'w') as countryMappingsFile:
        json.dump(countryMappings, countryMappingsFile)
        countryMappingsFile.close()
    sys.exit()


def listAllCountries():
    for key in countryDic.keys():
        print("Full Name : " + countryDic[key] + "      Country Code : " + key + '\n')
    sys.exit()


def findInterfaces():
    interfaceList = []
    interfaceDetailsList = []

    interfaces = subprocess.check_output("ls /sys/class/net", shell=True)
    interfaceString = str(interfaces)
    interfaceString = interfaceString[2:-3]
    interfaceList = interfaceString.split('\\n')

    for interface in interfaceList:
        showInterface = subprocess.check_output(["ip", "addr", "show", interface])
        showInterfaceStr = str(showInterface)
        ipaddress = showInterfaceStr[showInterfaceStr.find("inet") + 5:]
        ipaddress = ipaddress[:ipaddress.find(" ")]

        showInterfaceStr = showInterfaceStr[5:showInterfaceStr.find(">")+1]
        showInterfaceStr = showInterfaceStr.replace(":", "").replace("<", "").replace(">", "")

        showInterfaceList = showInterfaceStr.split(" ")
        if ipaddress != "":
            showInterfaceList.append(ipaddress)
        interfaceDetailsList.append(showInterfaceList)
    return interfaceDetailsList


def findVpnServerIP(server, port):
    # grab the ip address of vpnserver from the config file
    fullPath = "/usr/share/openpyn/files/" + server + ".nordvpn.com." + port + ".ovpn"
    with open(fullPath, 'r') as configFile:
        for line in configFile:
            if "remote " in line:
                vpnServerIp = line[7:]
                vpnServerIp = vpnServerIp[:vpnServerIp.find(" ")]
        configFile.close()
        return vpnServerIp


def applyFirewallRules(interfaceDetailsList, vpnServerIp):
    verifyRootAccess("Root access needed to modify 'iptables' rules")

    # Empty the INPUT and OUTPUT chain of any current rules
    subprocess.run(["sudo", "iptables", "-F", "OUTPUT"])
    subprocess.run(["sudo", "iptables", "-F", "INPUT"])

    # Allow all traffic out over the vpn tunnel
    subprocess.run("sudo iptables -A OUTPUT -o tun+ -j ACCEPT", shell=True)
    # accept traffic that comes through tun that you connect to
    subprocess.run("sudo iptables -A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -i tun+ -j ACCEPT", shell=True)
    for interface in interfaceDetailsList:

        # if interface is active with an IP in it, don't send DNS requests to it
        if len(interface) == 3 and "tun" not in interface[0]:
            subprocess.run(
                ["sudo", "iptables", "-A", "OUTPUT", "-o", interface[0], "-p",
                    "udp", "--destination-port", "53", "-j", "DROP"])
            # subprocess.run(
            #     ["sudo", "iptables", "-A", "OUTPUT", "-o", interface[0], "-p",
            #         "tcp", "--destination-port", "53", "-j", "DROP"])
            if interface[0] != "lo":
                # allow access to vpnServerIp
                subprocess.run(
                    ["sudo", "iptables", "-A", "OUTPUT", "-o", interface[0], "-d", vpnServerIp, "-j", "ACCEPT"])
                # talk to the vpnServer ip to connect to it
                subprocess.run(
                    ["sudo", "iptables", "-A", "INPUT", "-m", "conntrack",
                        "--ctstate", "ESTABLISHED,RELATED", "-i", interface[0], "-s", vpnServerIp, "-j", "ACCEPT"])

                # allow access to internal ip range
                # print("internal ip with range", interface[2])
                subprocess.run(
                    ["sudo", "iptables", "-A", "OUTPUT", "-o", interface[0], "-d",
                        interface[2], "-j", "ACCEPT"])
                subprocess.run(
                    ["sudo", "iptables", "-A", "INPUT", "-m", "conntrack",
                        "--ctstate", "ESTABLISHED,RELATED", "-i", interface[0], "-s", interface[2], "-j", "ACCEPT"])

    # Allow loopback traffic
    subprocess.run("sudo iptables -A INPUT -i lo -j ACCEPT", shell=True)
    subprocess.run("sudo iptables -A OUTPUT -o lo -j ACCEPT", shell=True)

    # best practice, stops spoofing
    subprocess.run("sudo iptables -A INPUT -s 127.0.0.0/8 -j DROP", shell=True)

    # Default action if no other rules match
    subprocess.run("sudo iptables -P OUTPUT DROP", shell=True)
    subprocess.run("sudo iptables -P INPUT DROP", shell=True)
    return


def verifyRootAccess(message):
    # Check that user has root priveleges.
    if os.getuid() != 0:
        print(message, '\n')


def connect(server, port, daemon):
    killVpnProcesses()   # kill existing openvpn processes
    print("CONNECTING TO SERVER", server, " ON PORT", port)
    verifyRootAccess("Root access needed to run 'openvpn'")

    osIsDebianBased = os.path.isfile("/sbin/resolvconf")
    # osIsDebianBased = False
    if osIsDebianBased:  # Debian Based OS
        # tunnel dns throught vpn by changing /etc/resolv.conf using
        # "update-resolv-conf.sh" to change the dns servers to NordVPN's.
        if daemon:
            subprocess.Popen(
                ["sudo", "openvpn", "--redirect-gateway", "--config", "/usr/share/openpyn/files/"
                    + server + ".nordvpn.com." + port + ".ovpn", "--auth-user-pass",
                    "/usr/share/openpyn/creds", "--script-security", "2",
                    "--up", "/usr/share/openpyn/update-resolv-conf.sh",
                    "--down", "/usr/share/openpyn/update-resolv-conf.sh", "--daemon"])
        else:
            try:
                subprocess.run(
                    "sudo openvpn --redirect-gateway --config" + " /usr/share/openpyn/files/"
                    + server + ".nordvpn.com." + port + ".ovpn --auth-user-pass \
                    /usr/share/openpyn/creds --script-security 2 --up \
                    /usr/share/openpyn/update-resolv-conf.sh --down \
                    /usr/share/openpyn/update-resolv-conf.sh", shell=True)
            except (KeyboardInterrupt) as err:
                print('\nShutting down safely, please wait until process exits\n')

    else:       # If not Debian Based
        print("'/sbin/resolvconf' not Found (NOT DEBIAN BASED OS?): Mannully Applying Patch" +
              " to Tunnel DNS Through The VPN Tunnel By Modifying '/etc/resolv.conf'")
        dnsPatch = subprocess.run(
            ["sudo", "/usr/share/openpyn/manual-dns-patch.sh"])

        if daemon:
            subprocess.Popen(
                ["sudo", "openvpn", "--redirect-gateway", "--config", "/usr/share/openpyn/files/"
                    + server + ".nordvpn.com." + port + ".ovpn",
                    "--auth-user-pass", "/usr/share/openpyn/creds", "--daemon"])
        else:
            try:
                subprocess.run(
                    "sudo openvpn --redirect-gateway --config" + " /usr/share/openpyn/files/"
                    + server + ".nordvpn.com." + port + ".ovpn --auth-user-pass \
                    /usr/share/openpyn/creds", shell=True)
            except (KeyboardInterrupt) as err:
                print('\nShutting down safely, please wait until process exits\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description="A python3 script to easily connect to and switch between, OpenVPN \
        servers hosted by NordVPN. Quickly Connect to the least busy servers (using current \
        data from Nordvpn website) with lowest latency from you. Tunnels DNS traffic through \
        the VPN which normally (when using OpenVPN with NordVPN) goes through your ISP's DNS \
        (still unencrypted, even if you use a thirdparty) and completely compromises Privacy!")
    parser.add_argument(
        '-v', '--version', action='version', version=__version__)
    parser.add_argument(
        '-s', '--server', help='server name, i.e. ca64 or au10',)
    parser.add_argument(
        '-u', '--udp', help='use port UDP-1194 instead of the default TCP-443',
        action='store_true')
    parser.add_argument(
        '-c', '--country-code', type=str, help='Specifiy Country Code with 2 letters, i.e au,')
    # use nargs='?' to make a positional arg optinal
    parser.add_argument(
        'country', nargs='?', help='Country Code can also be speficied without "-c,"\
         i.e "openpyn au"')
    parser.add_argument(
        '-d', '--daemon', help='Run script in the background as openvpn daemon',
        action='store_true')
    parser.add_argument(
        '-m', '--max-load', type=int, default=70, help='Specifiy load threashold, \
        rejects servers with more load than this, DEFAULT=70')
    parser.add_argument(
        '-t', '--top-servers', type=int, default=6, help='Specifiy the number of Top \
         Servers to choose from the NordVPN\'s Sever list for the given Country, These will be \
         Pinged. DEFAULT=6')
    parser.add_argument(
        '-p', '--pings', type=str, default="5", help='Specifiy number of pings \
        to be sent to each server to determine quality, DEFAULT=5')
    parser.add_argument(
        '-T', '--toppest-servers', type=int, default=3, help='After ping tests \
        the final server count to randomly choose a server from, DEFAULT=3')
    parser.add_argument(
        '-k', '--kill', help='Kill any running Openvnp process, very usefull \
        to kill openpyn process running in background with "-d" switch',
        action='store_true')
    parser.add_argument(
        '-x', '--kill-flush', help='Kill any running Openvnp process, AND Flush Iptables',
        action='store_true')
    parser.add_argument(
        '--update', help='Fetch the latest config files from nord\'s site',
        action='store_true')
    parser.add_argument(
        '--update-countries', help='Fetch the latest countries from nord\'s site\
        and update the country code mappings', action='store_true')
    parser.add_argument(
        '-l', '--list', dest="l_list", type=str, nargs='?', default="nope",
        help='If country code supplied ("-l us"): Displays all servers in a given\
        country with their current load and openvpn support status. Otherwise: \
        display all countries along with thier country-codes')
    parser.add_argument(
        '-f', '--force-fw-rules', help='Enfore Firewall rules to drop traffic when tunnel breaks\
        , Force disable DNS traffic going to any other interface', action='store_true')

    args = parser.parse_args()

    main(
        args.server, args.country_code, args.country, args.udp, args.daemon,
        args.max_load, args.top_servers, args.pings, args.toppest_servers,
        args.kill, args.kill_flush, args.update, args.l_list, args.update_countries,
        args.force_fw_rules)

sys.exit()

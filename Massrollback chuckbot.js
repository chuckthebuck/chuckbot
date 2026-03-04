// Mass rollback function with user-specified rate limit and bot tagging (if flagged)
/// Re-written by MolecularPilot, based on original by Mr.Z-man, John254, Writ Keeper and TheDJ 
///forked by Alachuckthebuck

if (typeof CTBContribsCheckboxInit === "undefined") {
    CTBContribsCheckboxInit = false;
}

if (typeof CTBRollbackPortlet === "undefined") {
    CTBRollbackPortlet = "p-cactions";
}


if (typeof CTBUseBuckbotQueue === "undefined") {
    CTBUseBuckbotQueue = true;
}

if (typeof CTBBuckbotEndpoint === "undefined") {
    CTBBuckbotEndpoint = null;
}

if (typeof CTBBuckbotToolName === "undefined") {
    CTBBuckbotToolName = "buckbot";
}


if (typeof CTBBuckbotAuthToken === "undefined") {
    CTBBuckbotAuthToken = null;
}

function generateRequestIdCTBMR() {
    if (window.crypto && window.crypto.randomUUID) {
        return window.crypto.randomUUID();
    }
    return "ctbmr-" + Date.now() + "-" + Math.floor(Math.random() * 1000000);
}

function collectRollbackTargetsCTBMR(rollbackLinks, fallbackUserName) {
    return rollbackLinks.map(function (_, el) {
        var $el = $(el);
        var titleMatch = /title=([^&]+)/.exec(el.href);
        var title = titleMatch ? decodeURIComponent(titleMatch[1]) : null;
        var userName = fallbackUserName;

        if (userName === null || userName === undefined) {
            userName = $el.parents("li:first").children("a.mw-anonuserlink").not(".mw-contributions-title").text();
        }

        return {
            title: title,
            user: userName
        };
    }).get().filter(function (target) {
        return !!target.title && !!target.user;
    });
}

function queueRollbackRequestCTBMR(requestPayload) {
    if (!CTBBuckbotEndpoint) {
        mw.notify("Buckbot endpoint is not configured. Set CTBBuckbotEndpoint to your Toolforge URL.");
        console.log("Buckbot rollback payload", requestPayload);
        return $.Deferred().reject("missing-endpoint").promise();
    }

    var headers = {
        "X-CTB-Request-Id": generateRequestIdCTBMR(),
        "X-CTB-Timestamp": Math.floor(Date.now() / 1000).toString(),
        "X-CTB-Requester": mw.config.get("wgUserName")
    };

    if (CTBBuckbotAuthToken) {
        headers.Authorization = "Bearer " + CTBBuckbotAuthToken;
    }

    return $.ajax({
        url: CTBBuckbotEndpoint,
        method: "POST",
        data: JSON.stringify(requestPayload),
        contentType: "application/json",
        dataType: "json",
        headers: headers
    });
}

function rollbackEverythingCTBMR(editSummary) {
    if (editSummary === null) {
        return false;
    }
    if (mw.config.get("wgRelevantUserName") === mw.config.get("wgUserName")) {
        if (!(confirm("You are about to roll back *all* of *your own* edits. Please note that this will be very difficult to undo. Are you *ABSOLUTELY SURE* you want to do this?"))) {
            return false;
        }
    }

    // Prompt user for the max rollbacks per minute
    var maxRollbacksPerMinute = parseInt(prompt("Enter the maximum number of rollbacks you want to perform in one minute:", "10"));
    if (isNaN(maxRollbacksPerMinute) || maxRollbacksPerMinute <= 0) {
        mw.notify("Invalid input! Please enter a positive integer.");
        return false;
    }

    mw.loader.using('mediawiki.api').done(function () {
        var rbMetadata = {};
        rbMetadata.api = new mw.Api();
        rbMetadata.userName = mw.config.get("wgRelevantUserName");
        rbMetadata.ipRange = (rbMetadata.userName === null);
        rbMetadata.titleRegex = /title=([^&]+)/;
        rbMetadata.editSummary = editSummary;
        var rollbackLinks = $("a[href*='action=rollback']");

        // Calculate delay per rollback based on user-specified limit
        var delayBetweenRollbacks = 60000 / maxRollbacksPerMinute; // Delay in ms for each rollback to stay within the limit (60,000ms = 1 minute)
        var rollbacksMade = 0;
        var startTime = Date.now(); // Time tracking for 1-minute window

        var targets = collectRollbackTargetsCTBMR(rollbackLinks, rbMetadata.userName);

        if (targets.length <= 0) {
            mw.notify("No rollback targets were found.");
            return;
        }

        if (CTBUseBuckbotQueue) {
            queueRollbackRequestCTBMR({
                command: "rollback",
                tool: CTBBuckbotToolName,
                wiki: mw.config.get("wgDBname"),
                mode: "all",
                requestedBy: mw.config.get("wgUserName"),
                relevantUser: rbMetadata.userName,
                maxRollbacksPerMinute: maxRollbacksPerMinute,
                editSummary: rbMetadata.editSummary,
                targets: targets,
                requestedAt: new Date().toISOString()
            }).done(function () {
                mw.notify("Queued " + targets.length + " rollback(s) for Buckbot.");
            }).fail(function () {
                mw.notify("Failed to send rollback request to Buckbot.");
            });
            return;
        }

        // Start the rollbacks with a delay
        rollbackLinks.each(function (ind, el) {
            var timeElapsed = Date.now() - startTime;

            if (rollbacksMade >= maxRollbacksPerMinute) {
                var waitForNextMinute = 60000 - timeElapsed;
                setTimeout(function () {
                    startTime = Date.now();
                    rollbacksMade = 0;
                    rollbackOneThingCTBMR(el, rbMetadata);
                }, waitForNextMinute);
            } else {
                setTimeout(function () {
                    rollbackOneThingCTBMR(el, rbMetadata);
                    rollbacksMade++;
                }, ind * delayBetweenRollbacks);
            }
        });
    });
    return false;
}

function rollbackSomeThingsCTBMR(editSummary) {
    if (editSummary === null) {
        return false;
    }

    // Prompt user for the max rollbacks per minute
    var maxRollbacksPerMinute = parseInt(prompt("Enter the maximum number of rollbacks you want to perform in one minute:", "10"));
    if (isNaN(maxRollbacksPerMinute) || maxRollbacksPerMinute <= 0) {
        mw.notify("Invalid input! Please enter a positive integer.");
        return false;
    }

    mw.loader.using('mediawiki.api').done(function () {
        var rbMetadata = {};
        rbMetadata.api = new mw.Api();
        rbMetadata.userName = mw.config.get("wgRelevantUserName");
        rbMetadata.titleRegex = /title=([^&]+)/;
        rbMetadata.editSummary = editSummary;
        var rollbackList = $("input.revdelIds:checked").parents("li.mw-contributions-current").find("a[href*='action=rollback']");

        if (rollbackList.length <= 0) {
            mw.notify("You didn't select any edits that could be rolled back!");
            return;
        }

        // Calculate delay per rollback based on user-specified limit
        var delayBetweenRollbacks = 60000 / maxRollbacksPerMinute; // Delay in ms for each rollback to stay within the limit (60,000ms = 1 minute)
        var rollbacksMade = 0;
        var startTime = Date.now(); // Time tracking for 1-minute window

        var targets = collectRollbackTargetsCTBMR(rollbackList, rbMetadata.userName);

        if (CTBUseBuckbotQueue) {
            queueRollbackRequestCTBMR({
                command: "rollback",
                tool: CTBBuckbotToolName,
                wiki: mw.config.get("wgDBname"),
                mode: "selected",
                requestedBy: mw.config.get("wgUserName"),
                relevantUser: rbMetadata.userName,
                maxRollbacksPerMinute: maxRollbacksPerMinute,
                editSummary: rbMetadata.editSummary,
                targets: targets,
                requestedAt: new Date().toISOString()
            }).done(function () {
                mw.notify("Queued " + targets.length + " rollback(s) for Buckbot.");
            }).fail(function () {
                mw.notify("Failed to send rollback request to Buckbot.");
            });
            return;
        }

        // Start the rollbacks with a delay
        rollbackList.each(function (ind, el) {
            var timeElapsed = Date.now() - startTime;

            if (rollbacksMade >= maxRollbacksPerMinute) {
                var waitForNextMinute = 60000 - timeElapsed;
                setTimeout(function () {
                    startTime = Date.now();
                    rollbacksMade = 0;
                    rollbackOneThingCTBMR(el, rbMetadata);
                }, waitForNextMinute);
            } else {
                setTimeout(function () {
                    rollbackOneThingCTBMR(el, rbMetadata);
                    rollbacksMade++;
                }, ind * delayBetweenRollbacks);
            }
        });
    });
}

function rollbackOneThingCTBMR(edit, rbMetadata) {
    var userName;
    // If in an anonymous IP range, determine the username for each edit individually.
    if (rbMetadata.userName === null) {
        userName = $(edit).parents("li:first").children("a.mw-anonuserlink").not(".mw-contributions-title").text();
    } else {
        userName = rbMetadata.userName;
    }
    var params = {
        markbot: true // Mark rollback as a bot edit
    };
    if (rbMetadata.editSummary !== '') {
        params.summary = rbMetadata.editSummary;
    }
    rbMetadata.api.rollback(decodeURIComponent(rbMetadata.titleRegex.exec(edit.href)[1]), userName, params).done(function () {
        $(edit).after("reverted");
        $(edit).remove();
    });
}

$(document).ready(function () {
    if (mw.config.get("wgCanonicalSpecialPageName") == "Contributions" && $("span.mw-rollback-link").length > 0) {
        mw.loader.using("mediawiki.util").done(function () {
            mw.util.addPortletLink(CTBRollbackPortlet, '#', "Rollback all", "ca-rollbackeverything", "rollback all edits displayed here");
            if (!CTBContribsCheckboxInit) {
                if ($("ul.mw-contributions-list .mw-revdelundel-link").length > 0) {
                    $("ul.mw-contributions-list .mw-revdelundel-link").each(function (ind, el) {
                        if ($(this).children("a").length > 0) {
                            var revId = /ids=(\d+)/.exec($(this).children("a").attr("href"))[1];
                            var pageTitle = /target=([^&]+)/.exec($(this).children("a").attr("href"))[1];
                            $(el).prepend("<input type='checkbox' name='" + decodeURIComponent(pageTitle) + "' class='revdelIds' value='" + revId + "'>&nbsp;");
                            $(el).children(".revdelIds").data("index", ind);
                        }
                    });
                } else {
                    $("ul.mw-contributions-list a.mw-changeslist-date").each(function (ind, el) {
                        $(el).before("<input type='checkbox' class='revdelIds'>&nbsp;");
                    });
                }
                CTBContribsCheckboxInit = true;
            }
            mw.util.addPortletLink(CTBRollbackPortlet, '#', "Rollback selected", "ca-rollbacksome", "rollback selected edits");
            $("#ca-rollbackeverything").click(function (event) {
                event.preventDefault();
                mw.loader.load('mediawiki.api'); //start loading, while the user is in the prompt
                return rollbackEverythingCTBMR(prompt("Rollback all edits: Enter an edit summary, or leave blank to use the default (or hit Cancel to cancel the rollback entirely)"));
            });
            $("#ca-rollbacksome").click(function (event) {
                event.preventDefault();
                mw.loader.load('mediawiki.api'); //start loading, while the user is in the prompt
                return rollbackSomeThingsCTBMR(prompt("Rollback selected edits: Enter an edit summary, or leave blank to use the default (or hit Cancel to cancel the rollback entirely)"));
            });
            $("#ca-rollbacksome").data("lastSelectedIndex", -1);

            $("input.revdelIds").off("click").click(function (ev) {
                var lastSelectedRevdel = $("#ca-rollbacksome").data("lastSelectedIndex");
                var newIndex = $(this).data("index");
                if (ev.shiftKey && lastSelectedRevdel >= 0) {
                    var checkboxArray = $("input.revdelIds");
                    var start = lastSelectedRevdel;
                    var stop = newIndex;
                    if (start < stop) {
                        for (var i = start; i < stop; i++) {
                            if (i != lastSelectedRevdel) {
                                $(checkboxArray[i]).prop("checked", !($(checkboxArray[i]).prop("checked")));
                            }
                        }
                    } else {
                        for (var i = start; i > stop; i--) {
                            if (i != lastSelectedRevdel) {
                                $(checkboxArray[i]).prop("checked", !($(checkboxArray[i]).prop("checked")));
                            }
                        
                        }
                    }
                }
                $("#ca-rollbacksome").data("lastSelectedIndex", newIndex);
            });

        });
    }
});

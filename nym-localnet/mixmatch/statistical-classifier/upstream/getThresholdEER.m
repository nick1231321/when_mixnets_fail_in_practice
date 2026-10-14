function [thr, FAR, FRR, EER] = getThresholdEER(trueAttempts, falseAttempts, polarity)
%[thr, FAR, FRR, EER] = getThresholdEER(trueAttempts, falseAttempts, polarity)
%
%This function returns the working point corresponding to the EER. It is
%assumed that the trueAttempts are generally higher than the falseAttempts,
%and both vectors are column vectors. Otherwise, polarity should be set to 
%1.
%

if (nargin == 2)
    polarity = -1;
end

tol = 1e-4;

minThr = min([trueAttempts; falseAttempts]);
maxThr = max([trueAttempts; falseAttempts]);
FAR = 0;
FRR = 1;
iter = 0;
% fprintf(1, "Polarity = %i\n", polarity);
while ((abs(FAR - FRR) > tol) && (iter < 60))
    iter = iter + 1;
    thr = (minThr + maxThr)/2;
    [FAR, FRR] = FARFRR(trueAttempts, falseAttempts, thr, polarity);
    % fprintf(1, "Threshold: %9f; FAR: %6.2f; FRR: %6.2f\n", thr, 100*FAR, 100*FRR);
    if (polarity ~= 1)
        if (FAR > FRR)
            minThr = thr;
        elseif (FRR > FAR)
            maxThr = thr;
        else
            EER = FAR;
            fprintf(1, 'Converged at iteration %i\n', iter);
            return;
        end
    else
        if (FAR > FRR)
            maxThr = thr;
        elseif (FRR > FAR)
            minThr = thr;
        else
            EER = FAR;
            return;
        end
    end
%     pause;
end
EER = (FAR + FRR)/2;

end
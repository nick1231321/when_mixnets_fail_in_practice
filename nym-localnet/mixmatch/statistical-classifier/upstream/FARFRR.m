function [FAR, FRR] = FARFRR(trueAttempts, falseAttempts, threshold, polarity)
% [FAR, FRR] = FARFRR(trueAttempts, falseAttempts, threshold, polarity)
%
% It returns the FAR and FRR, where FA is defined as:
%   count(falseAttempt >= threshold) when polarity is 1
%   count(falseAttempt <= threshold) when polarity is not 1,
% and FR is defined as:
%   count(trueAttempt < threshold) when polarity is 1
%   count(trueAttempt > threshold) when polarity is not 1.
%
if (nargin == 3)
    polarity = -1;
end

if (polarity == 1)
    FAR = sum(falseAttempts <= threshold)/length(falseAttempts);
    FRR = sum(trueAttempts > threshold)/length(trueAttempts);
else
    FAR = sum(falseAttempts >= threshold)/length(falseAttempts);
    FRR = sum(trueAttempts < threshold)/length(trueAttempts);
end

end